from time import sleep
import json

from doajtest.helpers import DoajTestCase

from flask_login import logout_user

from portality import models
from portality.background import BackgroundException
from portality.tasks.journal_bulk_edit import journal_manage, JournalBulkEditBackgroundTask

from doajtest.fixtures import JournalFixtureFactory, AccountFixtureFactory, EditorGroupFixtureFactory

TEST_JOURNAL_COUNT = 25

class TestTaskJournalBulkEdit(DoajTestCase):

    def setUp(self):
        super(TestTaskJournalBulkEdit, self).setUp()

        self.journals = []
        for j_src in JournalFixtureFactory.make_many_journal_sources(count=TEST_JOURNAL_COUNT):
            self.journals.append(models.Journal(**j_src))
            self.journals[-1].save()

        self.default_eg = EditorGroupFixtureFactory.setup_editor_group_with_editors()

        self.forbidden_accounts = [
            AccountFixtureFactory.make_editor_source()['id'],
            AccountFixtureFactory.make_assed1_source()['id'],
            AccountFixtureFactory.make_assed2_source()['id'],
            AccountFixtureFactory.make_assed3_source()['id']
        ]

        self._make_and_push_test_context(acc=models.Account(**AccountFixtureFactory.make_managing_editor_source()))

    def tearDown(self):
        super(TestTaskJournalBulkEdit, self).tearDown()

    def test_01_editor_group_successful_assign(self):
        """Bulk assign an editor group to a bunch of journals using a background task"""
        new_eg = EditorGroupFixtureFactory.setup_editor_group_with_editors(group_name='Test Editor Group')

        # test dry run
        summary = journal_manage({"query": {"terms": {"_id": [j.id for j in self.journals]}}}, editor_group=new_eg.name, dry_run=True)
        assert summary.as_dict().get("affected", {}).get("journals") == TEST_JOURNAL_COUNT, summary.as_dict()

        summary = journal_manage({"query": {"terms": {"_id": [j.id for j in self.journals]}}}, editor_group=new_eg.name, dry_run=False)
        assert summary.as_dict().get("affected", {}).get("journals") == TEST_JOURNAL_COUNT, summary.as_dict()

        sleep(1)

        job = models.BackgroundJob.all()[0]

        modified_journals = [j.pull(j.id) for j in self.journals]

        for ix, j in enumerate(modified_journals):
            assert j.editor_group == new_eg.name, \
                "modified_journals[{}].editor_group is {}" \
                "\nHere is the BackgroundJob audit log:\n{}"\
                    .format(ix, j.editor_group, json.dumps(job.audit, indent=2))

    def test_02_note_successful_add(self):
        # test dry run
        summary = journal_manage({"query": {"terms": {"_id": [j.id for j in self.journals]}}}, note="Test note", dry_run=True)
        assert summary.as_dict().get("affected", {}).get("journals") == TEST_JOURNAL_COUNT, summary.as_dict()

        summary = journal_manage({"query": {"terms": {"_id": [j.id for j in self.journals]}}}, note="Test note", dry_run=False)
        assert summary.as_dict().get("affected", {}).get("journals") == TEST_JOURNAL_COUNT, summary.as_dict()

        sleep(1)

        job = models.BackgroundJob.all()[0]

        modified_journals = [j.pull(j.id) for j in self.journals]
        for ix, j in enumerate(modified_journals):
            assert j, 'modified_journals[{}] is None, does not seem to exist?'
            assert j.notes, "modified_journals[{}] has no notes. It is \n{}".format(ix, json.dumps(j.data, indent=2))
            assert 'note' in j.notes[-1], json.dumps(j.notes[-1], indent=2)
            assert j.notes[-1]['note'] == 'Test note', \
                "The last note on modified_journals[{}] is {}\n" \
                "Here is the BackgroundJob audit log:\n{}".format(
                    ix, j.notes[-1]['note'], json.dumps(job.audit, indent=2)
                )

    def test_03_bulk_edit_not_admin(self):

        for acc_id in self.forbidden_accounts:
            logout_user()
            self._make_and_push_test_context(acc=models.Account.pull(acc_id))

            with self.assertRaises(BackgroundException):
                # test dry run
                r = journal_manage({"query": {"terms": {"_id": [j.id for j in self.journals]}}}, note="Test note", dry_run=True)

            with self.assertRaises(BackgroundException):
                r = journal_manage({"query": {"terms": {"_id": [j.id for j in self.journals]}}}, note="Test note", dry_run=False)

    def test_04_parameter_checks(self):
        # no params set at all
        params = {}
        assert JournalBulkEditBackgroundTask._job_parameter_check(params) is False, JournalBulkEditBackgroundTask._job_parameter_check(params)

        # ids set to None, but everything else is supplied
        params = {}
        JournalBulkEditBackgroundTask.set_param(params, 'ids', None)
        JournalBulkEditBackgroundTask.set_param(params, 'editor_group', 'test editor group')
        JournalBulkEditBackgroundTask.set_param(params, 'note', 'test note')

        assert JournalBulkEditBackgroundTask._job_parameter_check(params) is False, JournalBulkEditBackgroundTask._job_parameter_check(params)

        # ids set to [], but everything else is supplied
        JournalBulkEditBackgroundTask.set_param(params, 'ids', [])
        assert JournalBulkEditBackgroundTask._job_parameter_check(params) is False, JournalBulkEditBackgroundTask._job_parameter_check(params)

        # ids are supplied, but nothing else is supplied
        params = {}
        JournalBulkEditBackgroundTask.set_param(params, 'ids', ['123'])
        assert JournalBulkEditBackgroundTask._job_parameter_check(params) is False, JournalBulkEditBackgroundTask._job_parameter_check(params)

        # ids are supplied along with editor group only
        params = {}
        JournalBulkEditBackgroundTask.set_param(params, 'ids', ['123'])
        JournalBulkEditBackgroundTask.set_param(params, 'editor_group', 'test editor group')
        assert JournalBulkEditBackgroundTask._job_parameter_check(params) is True, JournalBulkEditBackgroundTask._job_parameter_check(params)

        # ids are supplied along with note only
        params = {}
        JournalBulkEditBackgroundTask.set_param(params, 'ids', ['123'])
        JournalBulkEditBackgroundTask.set_param(params, 'note', 'test note')
        assert JournalBulkEditBackgroundTask._job_parameter_check(params) is True, JournalBulkEditBackgroundTask._job_parameter_check(params)

        # everything is supplied
        params = {}
        JournalBulkEditBackgroundTask.set_param(params, 'ids', ['123'])
        JournalBulkEditBackgroundTask.set_param(params, 'editor_group', 'test editor group')
        JournalBulkEditBackgroundTask.set_param(params, 'note', 'test note')
        assert JournalBulkEditBackgroundTask._job_parameter_check(params) is True, JournalBulkEditBackgroundTask._job_parameter_check(params)