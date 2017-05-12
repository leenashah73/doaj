from copy import deepcopy
import json
from datetime import datetime

from flask_login import current_user

from portality import models, lock
from portality.core import app
from portality.formcontext import formcontext

from portality.tasks.redis_huey import main_queue
from portality.decorators import write_required

from portality.background import AdminBackgroundTask, BackgroundApi, BackgroundException, BackgroundSummary


def journal_manage(selection_query, dry_run=True, editor_group='', note=''):

    ids = JournalBulkEditBackgroundTask.resolve_selection_query(selection_query)
    if dry_run:
        JournalBulkEditBackgroundTask.check_admin_privilege(current_user.id)
        return BackgroundSummary(None, affected={"journals" : len(ids)})

    job = JournalBulkEditBackgroundTask.prepare(
        current_user.id,
        selection_query=selection_query,
        editor_group=editor_group,
        note=note,
        ids=ids
    )
    JournalBulkEditBackgroundTask.submit(job)

    affected = len(ids)
    job_id = None
    if job is not None:
        job_id = job.id
    return BackgroundSummary(job_id, affected={"journals" : affected})


class JournalBulkEditBackgroundTask(AdminBackgroundTask):

    __action__ = "journal_bulk_edit"

    @classmethod
    def _job_parameter_check(cls, params):
        # we definitely need "ids" defined
        # we need at least one of "editor_group" or "note" defined as well
        return bool(
            cls.get_param(params, 'ids') and \
            (cls.get_param(params, 'editor_group') or cls.get_param(params, 'note'))
        )

    def run(self):
        """
        Execute the task as specified by the background_job
        :return:
        """
        job = self.background_job
        params = job.params

        ids = self.get_param(params, 'ids')
        editor_group = self.get_param(params, 'editor_group')
        note = self.get_param(params, 'note')

        if not self._job_parameter_check(params):
            raise BackgroundException(u"{}.run run without sufficient parameters".format(self.__class__.__name__))

        for journal_id in ids:
            updated = False

            j = models.Journal.pull(journal_id)

            if j is None:
                job.add_audit_message(u"Journal with id {} does not exist, skipping".format(journal_id))
                continue

            fc = formcontext.JournalFormFactory.get_form_context(role="admin", source=j)

            if editor_group:
                job.add_audit_message(u"Setting editor_group to {x} for journal {y}".format(x=str(editor_group), y=journal_id))

                # set the editor group
                f = fc.form.editor_group
                f.data = editor_group

                # clear the editor
                ed = fc.form.editor
                ed.data = None

                updated = True
                
            if note:
                job.add_audit_message(u"Adding note to for journal {y}".format(y=journal_id))
                fc.form.notes.append_entry(
                    {'date': datetime.now().strftime(app.config['DEFAULT_DATE_FORMAT']), 'note': note}
                )
                updated = True
            
            if updated:
                if fc.validate():
                    try:
                        fc.finalise()
                    except formcontext.FormContextException as e:
                        job.add_audit_message(u"Form context exception while bulk editing journal {} :\n{}".format(journal_id, e.message))
                else:
                    data_submitted = {}
                    for affected_field_name in fc.form.errors.keys():
                        affected_field = getattr(fc.form, affected_field_name,
                                                 ' Field {} does not exist on form. '.format(affected_field_name))
                        if isinstance(affected_field, basestring):  # ideally this should never happen, an error should not be reported on a field that is not present on the form
                            data_submitted[affected_field_name] = affected_field
                            continue

                        data_submitted[affected_field_name] = affected_field.data
                    job.add_audit_message(
                        u"Data validation failed while bulk editing journal {} :\n"
                        u"{}\n\n"
                        u"The data from the fields with the errors is:\n{}".format(
                            journal_id, json.dumps(fc.form.errors), json.dumps(data_submitted)
                        )
                    )

    def cleanup(self):
        """
        Cleanup after a successful OR failed run of the task
        :return:
        """
        job = self.background_job
        params = job.params
        ids = self.get_param(params, 'ids')
        username = job.user

        lock.batch_unlock("journal", ids, username)

    @classmethod
    def resolve_selection_query(cls, selection_query):
        q = deepcopy(selection_query)
        q["_source"] = False
        iterator = models.Journal.iterate(q=q, page_size=5000, wrap=False)
        return [j['_id'] for j in iterator]

    @classmethod
    def prepare(cls, username, **kwargs):
        """
        Take an arbitrary set of keyword arguments and return an instance of a BackgroundJob,
        or fail with a suitable exception

        :param kwargs: arbitrary keyword arguments pertaining to this task type
        :return: a BackgroundJob instance representing this task
        """

        super(JournalBulkEditBackgroundTask, cls).prepare(username, **kwargs)

        # first prepare a job record
        job = models.BackgroundJob()
        job.user = username
        job.action = cls.__action__

        refs = {}
        cls.set_reference(refs, "selection_query", json.dumps(kwargs['selection_query']))
        job.reference = refs

        params = {}
        cls.set_param(params, 'ids', kwargs['ids'])
        cls.set_param(params, 'editor_group', kwargs.get('editor_group', ''))
        cls.set_param(params, 'note', kwargs.get('note', ''))

        if not cls._job_parameter_check(params):
            raise BackgroundException(u"{}.prepare run without sufficient parameters".format(cls.__name__))

        job.params = params

        # now ensure that we have the locks for all the journals
        # will raise an exception if this fails
        lock.batch_lock("journal", kwargs['ids'], username, timeout=app.config.get("BACKGROUND_TASK_LOCK_TIMEOUT", 3600))

        return job

    @classmethod
    def submit(cls, background_job):
        """
        Submit the specified BackgroundJob to the background queue

        :param background_job: the BackgroundJob instance
        :return:
        """
        background_job.save(blocking=True)
        journal_bulk_edit.schedule(args=(background_job.id,), delay=10)


@main_queue.task()
@write_required(script=True)
def journal_bulk_edit(job_id):
    job = models.BackgroundJob.pull(job_id)
    task = JournalBulkEditBackgroundTask(job)
    BackgroundApi.execute(task)