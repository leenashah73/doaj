import time

from portality import constants
from doajtest.fixtures.v2 import ApplicationFixtureFactory, JournalFixtureFactory
from doajtest.fixtures import AccountFixtureFactory
from doajtest.helpers import DoajTestCase
from portality.api.v2.common import Api401Error, Api400Error, Api404Error, Api403Error
from portality.api.v2.crud.applications import ApplicationsCrudApi
from portality.api.v2.data_objects.application import IncomingApplication, OutgoingApplication
from portality.lib.dataobj import DataStructureException
from portality.formcontext import FormContextException, formcontext


def mock_finalise_exception(self, *args, **kwargs):
    raise FormContextException("test exception")


def mock_custom_validate_always_pass(self, *args, **kwargs):
    return


class TestCrudApplication(DoajTestCase):

    def setUp(self):
        self.old_finalise = formcontext.FormContext.finalise
        self.old_custom_validate = IncomingApplication.custom_validate
        super(TestCrudApplication, self).setUp()

    def tearDown(self):
        formcontext.FormContext.finalise = self.old_finalise
        IncomingApplication.custom_validate = self.old_custom_validate
        super(TestCrudApplication, self).tearDown()

    def test_01_incoming_application_do(self):
        # make a blank one
        ia = IncomingApplication()

        # make one from an incoming application model fixture
        data = ApplicationFixtureFactory.incoming_application()
        ia = IncomingApplication(data)

        # make another one that's broken
        data = ApplicationFixtureFactory.incoming_application()
        del data["bibjson"]["title"]
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # now progressively remove the conditionally required/advanced validation stuff
        #
        # missing identifiers

        # no issns specified
        data = ApplicationFixtureFactory.incoming_application()
        del data["bibjson"]["pissn"]
        del data["bibjson"]["eissn"]
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)


        # issns the same (but not normalised the same)
        data["bibjson"]["pissn"] = "12345678"
        data["bibjson"]["eissn"] = "1234-5678"
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # no homepage link
        data = ApplicationFixtureFactory.incoming_application()
        del data["bibjson"]["ref"]["journal"]
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # plagiarism detection but no url
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["plagiarism"]["detection"] = True
        del data["bibjson"]["plagiarism"]["url"]
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # embedded licence but no url
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["article"]["embedded_licence"] = True
        del data["bibjson"]["article"]["embedded_licence_url"]
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # author copyright and no link
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["copyright"]["author_retains"] = True
        del data["bibjson"]["copyright"]["url"]
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # invalid domain in archiving_policy
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["preservation"]["has_preservation"] = True
        data["bibjson"]["preservation"]["url"] = "abcd://abcd"
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # too many keywords
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["keywords"] = ["one", "two", "three", "four", "five", "six", "seven"]
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

    def test_02_create_application_success(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        data["admin"]["application_status"] = "on_hold"
        data["admin"]["owner"] = "someaccount"

        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # check that it got created with the right properties
        assert isinstance(a, models.Application)
        assert a.id != "ignore_me"
        assert a.created_date != "2001-01-01T00:00:00Z"
        assert a.last_updated != "2001-01-01T00:00:00Z"
        assert a.admin.applicant.get("name") == "Tester"
        assert a.admin.applicant.get("email") == "test@test.com"
        assert a.owner == "test"
        assert a.suggested_on is not None
        assert len(a.bibjson().keywords) > 1

        # check the stuff that should default
        assert a.application_status == "pending"
        assert a.owner == "test"

        preservation = a.bibjson().perservation
        assert len(preservation.get("service")) == 2
        assert preservation.get("national_library") == "Trinity"
        assert "CLOCKSS" in preservation.get("service")
        assert "LOCKSS" in preservation.get("service")
        assert "A safe place" in preservation.get("service")

        time.sleep(2)

        s = models.Application.pull(a.id)
        assert s is not None

    def test_02a_create_application_success_variations(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")

        # try with only one issn
        data["bibjson"]["pissn"] = "1234-5678"

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # check that it got created successfully
        assert isinstance(a, models.Application)

        time.sleep(2)

        s = models.Application.pull(a.id)
        assert s is not None


    def test_03_create_application_fail(self):
        # if the account is dud
        with self.assertRaises(Api401Error):
            data = ApplicationFixtureFactory.incoming_application()
            a = ApplicationsCrudApi.create(data, None)

        # if the data is bust
        with self.assertRaises(Api400Error):
            account = models.Account()
            account.set_id("test")
            account.set_name("Tester")
            account.set_email("test@test.com")
            data = {"some" : {"junk" : "data"}}
            a = ApplicationsCrudApi.create(data, account)

        # if a formcontext exception is raised on finalise
        formcontext.FormContext.finalise = mock_finalise_exception
        with self.assertRaises(Api400Error):
            data = ApplicationFixtureFactory.incoming_application()
            del data["admin"]["current_journal"]
            publisher = models.Account(**AccountFixtureFactory.make_publisher_source())
            try:
                a = ApplicationsCrudApi.create(data, publisher)
            except Api400Error as e:
                assert str(e) == "test exception"
                raise
        formcontext.FormContext.finalise = self.old_finalise

        # validation fails on the formcontext
        IncomingApplication.custom_validate = mock_custom_validate_always_pass
        with self.assertRaises(Api400Error):
            data = ApplicationFixtureFactory.incoming_application()
            del data["admin"]["current_journal"]
            # a duff email should trigger the form validation failure
            data["admin"]["applicant"]["email"] = "not an email address"
            publisher = models.Account(**AccountFixtureFactory.make_publisher_source())
            try:
                a = ApplicationsCrudApi.create(data, publisher)
            except Api400Error as e:
                raise
        IncomingApplication.custom_validate = self.old_custom_validate

        # issns are the same
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        data["bibjson"]["pissn"] = "1234-5678"
        data["bibjson"]["eissn"] = "1234-5678"

        with self.assertRaises(Api400Error):
            publisher = models.Account(**AccountFixtureFactory.make_publisher_source())
            try:
                a = ApplicationsCrudApi.create(data, publisher)
            except Api400Error as e:
                raise

    def test_03b_create_update_request_fail(self):
        # update request target not found
        with self.assertRaises(Api404Error):
            data = ApplicationFixtureFactory.incoming_application()
            publisher = models.Account(**AccountFixtureFactory.make_publisher_source())
            try:
                a = ApplicationsCrudApi.create(data, publisher)
            except Api404Error as e:
                raise

        # if a formcontext exception is raised on finalise
        publisher = models.Account(**AccountFixtureFactory.make_publisher_source())
        journal = models.Journal(**JournalFixtureFactory.make_journal_source(in_doaj=True))
        journal.set_id(journal.makeid())
        journal.set_owner(publisher.id)
        journal.save(blocking=True)
        formcontext.FormContext.finalise = mock_finalise_exception
        with self.assertRaises(Api400Error):
            data = ApplicationFixtureFactory.incoming_application()
            data["admin"]["current_journal"] = journal.id

            try:
                a = ApplicationsCrudApi.create(data, publisher)
            except Api400Error as e:
                assert str(e) == "test exception"
                raise
        formcontext.FormContext.finalise = self.old_finalise

        # validation fails on the formcontext
        publisher = models.Account(**AccountFixtureFactory.make_publisher_source())
        journal = models.Journal(**JournalFixtureFactory.make_journal_source(in_doaj=True))
        journal.set_id(journal.makeid())
        journal.set_owner(publisher.id)
        journal.save(blocking=True)
        IncomingApplication.custom_validate = mock_custom_validate_always_pass
        with self.assertRaises(Api400Error):
            data = ApplicationFixtureFactory.incoming_application()
            # duff submission charges url should trip the validator
            data["bibjson"]["other_charges_url"] = "not a url!"
            data["admin"]["current_journal"] = journal.id
            try:
                a = ApplicationsCrudApi.create(data, publisher)
            except Api400Error as e:
                raise

    def test_03c_update_update_request_fail(self):
        # update request target in disallowed status
        journal = models.Journal(**JournalFixtureFactory.make_journal_source(in_doaj=True))
        journal.set_id(journal.makeid())
        journal.save(blocking=True)
        with self.assertRaises(Api404Error):
            data = ApplicationFixtureFactory.incoming_application()
            data["admin"]["current_journal"] = journal.id
            publisher = models.Account(**AccountFixtureFactory.make_publisher_source())
            try:
                a = ApplicationsCrudApi.create(data, publisher)
            except Api404Error as e:
                raise

    def test_03a_create_application_dryrun(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")

        # call create on the object, with the dry_run flag set
        a = ApplicationsCrudApi.create(data, account, dry_run=True)

        time.sleep(2)

        # now check that the application index remains empty
        ss = [x for x in models.Application.iterall()]
        assert len(ss) == 0

    def test_04_coerce(self):
        data = ApplicationFixtureFactory.incoming_application()

        # first test a load of successes
        data["bibjson"]["publisher"]["country"] = "Bangladesh"
        data["bibjson"]["apc"]["max"][0]["currency"] = "Taka"
        data["bibjson"]["publication_time_weeks"] = "15"
        data["bibjson"]["language"] = ["French", "English"]
        data["bibjson"]["pid_scheme"]["has_pid_scheme"] = True
        data["bibjson"]["pid_scheme"]["scheme"] = ["doi", "HandleS", "something"]
        data["bibjson"]["license"]["type"] = "cc"
        data["bibjson"]["licence"]["BY"] = True
        data["bibjson"]["deposit_policy"]["has_policy"] = True
        data["bibjson"]["deposit_policy"]["services"] = ["sherpa/romeo", "other"]

        ia = IncomingApplication(data)

        assert ia.bibjson.publisher.country == "BD"
        assert ia.bibjson.apc.max[0].currency == "BDT"
        assert isinstance(ia.bibjson.title, str)
        assert ia.bibjson.publication_time_weeks == 15
        assert "fr" in ia.bibjson.language
        assert "en" in ia.bibjson.language
        assert len(ia.bibjson.language) == 2
        assert ia.bibjson.pid_scheme.scheme[0] == "DOI"
        assert ia.bibjson.pid_scheme.scheme[1] == "Handles"
        assert ia.bibjson.pid_scheme.scheme[2] == "something"
        assert ia.bibjson.license.type == "CC BY"
        assert ia.bibjson.license["BY"]
        assert ia.bibjson.deposit_policy.services[0] == "Sherpa/Romeo"
        assert ia.bibjson.deposit_policy.services[1] == "other"

        # now test some failures
        # invalid country name
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["publisher"]["country"] = "LandLand"
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # invalid currency name
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["apc"]["max"][0]["currency"] = "Wonga"
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # an invalid url
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["apc"]["url"] = "Two streets down on the left"
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # invalid bool
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["has_apc"] = "Yes"
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # invalid int
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["publication_time_weeks"] = "Fifteen"
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

        # invalid language code
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["language"] = ["Hagey Pagey"]
        with self.assertRaises(DataStructureException):
            ia = IncomingApplication(data)

    def test_05_outgoing_application_do(self):
        # make a blank one
        oa = OutgoingApplication()

        # make one from an incoming application model fixture
        data = ApplicationFixtureFactory.make_update_request_source()
        ap = models.Application(**data)
        oa = OutgoingApplication.from_model(ap)

        # check that it does not contain information that it shouldn't
        assert oa.data.get("index") is None
        assert oa.data.get("history") is None
        assert oa.data.get("admin", {}).get("notes") is None
        assert oa.data.get("admin", {}).get("editor_group") is None
        assert oa.data.get("admin", {}).get("editor") is None
        assert oa.data.get("admin", {}).get("seal") is None
        assert oa.data.get("admin", {}).get("related_journal") is None

        # check that it does contain admin information that it should
        assert oa.data.get("admin", {}).get("current_journal") is not None

    def test_06_retrieve_application_success(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.make_update_request_source()
        ap = models.Application(**data)
        ap.save()
        time.sleep(2)

        account = models.Account()
        account.set_id(ap.owner)
        account.set_name("Tester")
        account.set_email("test@test.com")

        # call retrieve on the object
        a = ApplicationsCrudApi.retrieve(ap.id, account)

        # check that we got back the object we expected
        assert isinstance(a, OutgoingApplication)
        assert a.id == ap.id

    def test_07_retrieve_application_fail(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.make_update_request_source()
        ap = models.Application(**data)
        ap.save()
        time.sleep(2)

        # no user
        with self.assertRaises(Api401Error):
            a = ApplicationsCrudApi.retrieve(ap.id, None)

        # wrong user
        account = models.Account()
        account.set_id("asdklfjaioefwe")
        with self.assertRaises(Api404Error):
            a = ApplicationsCrudApi.retrieve(ap.id, account)

        # non-existant application
        account = models.Account()
        account.set_id(ap.id)
        with self.assertRaises(Api404Error):
            a = ApplicationsCrudApi.retrieve("ijsidfawefwefw", account)

    def test_08_update_application_success(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")
        account.add_role("publisher")

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # let the index catch up
        time.sleep(2)

        # get a copy of the newly created version for use in assertions later
        created = models.Application.pull(a.id)

        # now make an updated version of the object
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        data["bibjson"]["title"] = "An updated title"

        # call update on the object
        a2 = ApplicationsCrudApi.update(a.id, data, account)
        assert a2 != a

        # let the index catch up
        time.sleep(2)

        # get a copy of the updated version
        updated = models.Application.pull(a.id)

        # now check the properties to make sure the update tool
        assert updated.bibjson().title == "An updated title"
        assert updated.created_date == created.created_date

    def test_09_update_application_fail(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # let the index catch up
        time.sleep(2)

        # get a copy of the newly created version for use in assertions later
        created = models.Application.pull(a.id)

        # now make an updated version of the object
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        data["bibjson"]["title"] = "An updated title"

        # call update on the object in various context that will fail

        # without an account
        with self.assertRaises(Api401Error):
            ApplicationsCrudApi.update(a.id, data, None)

        # with the wrong account
        account.set_id("other")
        with self.assertRaises(Api404Error):
            ApplicationsCrudApi.update(a.id, data, account)

        # on the wrong id
        account.set_id("test")
        with self.assertRaises(Api404Error):
            ApplicationsCrudApi.update("adfasdfhwefwef", data, account)

        # on one with a disallowed workflow status
        created.set_application_status(constants.APPLICATION_STATUS_ACCEPTED)
        created.save()
        time.sleep(2)
        account.add_role("publisher")

        with self.assertRaises(Api403Error):
            ApplicationsCrudApi.update(a.id, data, account)

    def test_10_delete_application_success(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")
        account.add_role("publisher")

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # let the index catch up
        time.sleep(2)

        # now delete it
        ApplicationsCrudApi.delete(a.id, account)

        # let the index catch up
        time.sleep(2)

        ap = models.Application.pull(a.id)
        assert ap is None

    def test_11_delete_application_fail(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")
        account.add_role("publisher")

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # let the index catch up
        time.sleep(2)

        # get a copy of the newly created version for use in test later
        created = models.Application.pull(a.id)

        # call delete on the object in various context that will fail

        # without an account
        with self.assertRaises(Api401Error):
            ApplicationsCrudApi.delete(a.id, None)

        # with the wrong account
        account.set_id("other")
        with self.assertRaises(Api404Error):
            ApplicationsCrudApi.delete(a.id, account)

        # on the wrong id
        account.set_id("test")
        with self.assertRaises(Api404Error):
            ApplicationsCrudApi.delete("adfasdfhwefwef", account)

        # on one with a disallowed workflow status
        created.set_application_status(constants.APPLICATION_STATUS_ACCEPTED)
        created.save()
        time.sleep(2)

        with self.assertRaises(Api403Error):
            ApplicationsCrudApi.delete(a.id, account)

    def test_12_delete_application_dryrun(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        del data["admin"]["current_journal"]
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")
        account.add_role("publisher")

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # let the index catch up
        time.sleep(2)

        # now delete it with the dry run flag
        ApplicationsCrudApi.delete(a.id, account, dry_run=True)

        # let the index catch up
        time.sleep(2)

        ap = models.Application.pull(a.id)
        assert ap is not None

    def test_13_create_application_update_request_success(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")
        account.add_role("publisher")
        account.save(blocking=True)

        journal = models.Journal(**JournalFixtureFactory.make_journal_source(in_doaj=True))
        journal.bibjson().remove_identifiers()
        journal.bibjson().add_identifier(journal.bibjson().E_ISSN, "9999-8888")
        journal.bibjson().add_identifier(journal.bibjson().P_ISSN, "7777-6666")
        journal.bibjson().title = "not changed"
        journal.set_id(data["admin"]["current_journal"])
        journal.set_owner(account.id)
        journal.save(blocking=True)

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # check that it got created with the right properties
        assert isinstance(a, models.Application)
        assert a.id != "ignore_me"
        assert a.created_date != "2001-01-01T00:00:00Z"
        assert a.last_updated != "2001-01-01T00:00:00Z"
        assert a.admin.applicant.get("name") == "Tester"           # The suggester should be the owner of the existing journal
        assert a.admin.applicant.get("email") == "test@test.com"
        assert a.owner == "test"
        assert a.suggested_on is not None
        assert a.bibjson().issns() == ["9999-8888", "7777-6666"] or a.bibjson().issns() == ["7777-6666", "9999-8888"]
        assert a.bibjson().title == "not changed"

        # also, because it's a special case, check the archiving_policy
        preservation_services = a.bibjson().preservation_services
        assert len(preservation_services) == 4
        assert "CLOCKSS" in preservation_services
        assert "LOCKSS" in preservation_services
        assert "Trinity" in preservation_services, "Expected: 'Trinity', found: {}".format(preservation_services)

        time.sleep(2)

        s = models.Application.pull(a.id)
        assert s is not None

    def test_14_create_application_update_request_fail(self):
        data = ApplicationFixtureFactory.incoming_application()

        journal = models.Journal(**JournalFixtureFactory.make_journal_source(in_doaj=True))
        journal.bibjson().remove_identifiers()
        journal.bibjson().add_identifier(journal.bibjson().E_ISSN, "9999-8888")
        journal.bibjson().add_identifier(journal.bibjson().P_ISSN, "7777-6666")
        journal.bibjson().title = "not changed"
        journal.set_id(data["admin"]["current_journal"])
        journal.set_owner("test")
        journal.save(blocking=True)

        # if the account is dud
        with self.assertRaises(Api401Error):
            a = ApplicationsCrudApi.create(data, None)

        # if the data is bust
        with self.assertRaises(Api400Error):
            account = models.Account()
            account.set_id("test")
            account.set_name("Tester")
            account.set_email("test@test.com")
            data = {"some" : {"junk" : "data"}}
            a = ApplicationsCrudApi.create(data, account)

    def test_15_create_application_update_request_dryrun(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")
        account.add_role("publisher")

        journal = models.Journal(**JournalFixtureFactory.make_journal_source(in_doaj=True))
        journal.bibjson().remove_identifiers()
        journal.bibjson().add_identifier(journal.bibjson().E_ISSN, "9999-8888")
        journal.bibjson().add_identifier(journal.bibjson().P_ISSN, "7777-6666")
        journal.bibjson().title = "not changed"
        journal.set_id(data["admin"]["current_journal"])
        journal.set_owner(account.id)
        journal.save(blocking=True)

        # call create on the object, with the dry_run flag set
        a = ApplicationsCrudApi.create(data, account, dry_run=True)

        time.sleep(2)

        # now check that the application index remains empty
        ss = [x for x in models.Application.iterall()]
        assert len(ss) == 0

    def test_16_update_application_update_request_success(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")
        account.add_role("publisher")

        journal = models.Journal(**JournalFixtureFactory.make_journal_source(in_doaj=True))
        journal.bibjson().remove_identifiers()
        journal.bibjson().add_identifier(journal.bibjson().E_ISSN, "9999-8888")
        journal.bibjson().add_identifier(journal.bibjson().P_ISSN, "7777-6666")
        journal.bibjson().title = "not changed"
        journal.set_id(data["admin"]["current_journal"])
        journal.set_owner(account.id)
        journal.save(blocking=True)

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # let the index catch up
        time.sleep(2)

        # get a copy of the newly created version for use in assertions later
        created = models.Application.pull(a.id)

        # now make an updated version of the object
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["title"] = "An updated title"
        data["bibjson"]["publisher"]["name"] = "An updated publisher"

        # call update on the object
        a2 = ApplicationsCrudApi.update(a.id, data, account)
        assert a2 != a

        # let the index catch up
        time.sleep(2)

        # get a copy of the updated version
        updated = models.Application.pull(a.id)

        # now check the properties to make sure the update tool
        assert updated.bibjson().title == "not changed"
        assert updated.bibjson().publisher.name == "An updated publisher"
        assert updated.created_date == created.created_date

    def test_17_update_application_update_request_fail(self):
        # set up all the bits we need
        data = ApplicationFixtureFactory.incoming_application()
        account = models.Account()
        account.set_id("test")
        account.set_name("Tester")
        account.set_email("test@test.com")
        account.add_role("publisher")

        journal = models.Journal(**JournalFixtureFactory.make_journal_source(in_doaj=True))
        journal.bibjson().remove_identifiers()
        journal.bibjson().add_identifier(journal.bibjson().E_ISSN, "9999-8888")
        journal.bibjson().add_identifier(journal.bibjson().P_ISSN, "7777-6666")
        journal.bibjson().title = "not changed"
        journal.set_id(data["admin"]["current_journal"])
        journal.set_owner(account.id)
        journal.save(blocking=True)

        # call create on the object (which will save it to the index)
        a = ApplicationsCrudApi.create(data, account)

        # let the index catch up
        time.sleep(2)

        # get a copy of the newly created version for use in assertions later
        created = models.Application.pull(a.id)

        # now make an updated version of the object
        data = ApplicationFixtureFactory.incoming_application()
        data["bibjson"]["title"] = "An updated title"
        data["bibjson"]["publisher"]["name"] = "An updated publisher"

        # call update on the object in various context that will fail

        # without an account
        with self.assertRaises(Api401Error):
            ApplicationsCrudApi.update(a.id, data, None)

        # with the wrong account
        account.set_id("other")
        with self.assertRaises(Api404Error):
            ApplicationsCrudApi.update(a.id, data, account)

        # on the wrong id
        account.set_id("test")
        with self.assertRaises(Api404Error):
            ApplicationsCrudApi.update("adfasdfhwefwef", data, account)

        # with the wrong user role
        account.remove_role("publisher")
        with self.assertRaises(Api404Error):
            ApplicationsCrudApi.update(a.id, data, account)
        account.add_role("publisher")

        # on one with a disallowed workflow status
        created.set_application_status(constants.APPLICATION_STATUS_ACCEPTED)
        created.save(blocking=True)

        with self.assertRaises(Api403Error):
            ApplicationsCrudApi.update(a.id, data, account)

        # one with the wrong data structure
        data["flibble"] = "whatever"
        with self.assertRaises(Api400Error):
            ApplicationsCrudApi.update(a.id, data, account)
        del data["flibble"]

        # one where we tried to change the current_journal
        data["admin"]["current_journal"] = "owqierqwoieqwoijefwq"
        with self.assertRaises(Api400Error):
            ApplicationsCrudApi.update(a.id, data, account)