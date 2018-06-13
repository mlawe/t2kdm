"""Module to deal with regular replication, checking and general housekeeping tasks."""

import argparse
from six.moves import configparser
from six import print_
import t2kdm
from contextlib import contextmanager
import sys, os
from datetime import datetime, timedelta, tzinfo
import posixpath

class UTC(tzinfo):
    """UTC class, because pytz would be overkill"""

    def utcoffset(self, dt):
        return timedelta(0)

    def tzname(self, dt):
        return "UTC"

    def dst(self, dt):
        return timedelta(0)
utc = UTC()

def pid_running(pid):
    """Return `True` is a process with the given PID is running."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True

class Task(object):
    """Class to organise what need to be done when."""

    def __init__(self, **kwargs):
        """Initialise a basic task.

        All tasks support these basic keyword arguments:

            frequency = 'daily' | 'weekly' | 'monthly'
                How often is the task to be done? Default: 'weekly'

            logfile
                If provided, redirect all output of the task to this file.

        """

        # Handle basic keyword arguments
        self.frequency = kwargs.pop('frequency', 'weekly')
        if self.frequency not in ['daily', 'weekly', 'monthly']:
            raise ValueError("Illegal frequency!")
        self.logfile = kwargs.pop('logfile', None)

        self.last_done = None
        self.state = None

    def get_period(self):
        """Get the time period (1/frequency) of the task."""
        if self.frequency  == 'daily':
            return timedelta(1)
        elif self.frequency  == 'weekly':
            return timedelta(7)
        elif self.frequency  == 'monthly':
            return timedelta(30)
        else:
            raise ValueError("Illegal frequency!")

    @contextmanager
    def redirected_output(self):
        if self.logfile is not None:
            # Redirect output to logfile
            stdout = sys.stdout
            stderr = sys.stderr
            with open(self.logfile, 'at')as f:
                sys.stdout = f
                sys.stderr = f
                try:
                    yield
                finally:
                   sys.stdout = stdout
                   sys.stderr = stdout
        else:
            # Nothing to do
            yield

    @staticmethod
    def parse_config(section, option, value):
        """Parse a configuration string into kwargs for `__init__`."""
        if value is None:
            arguments = []
        else:
            arguments = value.split()

        kwargs = {}

        if 'monthly' in arguments:
            kwargs['frequency'] = 'monthly'
        if 'weekly' in arguments:
            kwargs['frequency'] = 'weekly'
        if 'daily' in arguments:
            kwargs['frequency'] = 'daily'

        return kwargs

    def _pre_do(self, id=None):
        """Bookkeeping when starting a task."""
        now = datetime.now(utc)
        self.state = 'STARTED'
        self.last_done = now
        if id is None:
            self.last_id = os.getpid()
        else:
            self.last_id = id

    def _do(self):
        """Internal function that does what needs to be done.

        Must be implemented in inheriting classes.
        """
        raise NotImplementedError()


    def do(self, id=None):
        """Actually do the task."""
        self._pre_do(id=id)

        with self.redirected_output():
            self._do()

        # We fail here because the base class does not do anything
        # If someone calls this method, something probably went wrong somewhere...
        self._post_do(state='FAILED', id=id)

    def _post_do(self, state='DONE', id=None):
        """Bookkeeping when finishing a task."""
        self.state = state
        if id is None:
            self.last_id = os.getpid()
        else:
            self.last_id = id

    def get_due(self):
        """Calculate how due the task is.

        Return value is a float:

            due < 0     -> Task is not due to be done
            due == 0    -> Task should be done excactly now (on the second!)
            due > 0     -> Task is overdue

        The dueness is scaled by the intended period of task execution,
        i.e. a weekly task that has been run 9 days ago is less due than a daily task
        that has last been run 1.5 days ago.
        """

        now = datetime.now(utc)
        day = 24*3600 # seconds per day
        week = 7*day # seconds per week
        T = self.get_period()

        # Everything sucks on python 2.6...
        # Define a function that returns the seconds in a timedelta
        sec = lambda td: float(td.seconds + (td.days * day))

        if self.last_done is None:
            # If no last execution is known, assume the task needs to be done since a week ago
            return week / sec(T)
        else:
            return sec(now - (self.last_done + T)) / sec(T)

    def get_id(self):
        """Return a string that identifies the task."""
        return str(self)

    def __str__(self):
        """Return a string to identify the task by."""
        return '%s_Task'%(self.frequency,)

class ReplicationTask(Task):
    """Replicate a folder to a SE."""

    def __init__(self, **kwargs):
        self.path = kwargs.pop('path')
        self.destination = kwargs.pop('destination')
        Task.__init__(self, **kwargs)

    @staticmethod
    def parse_config(section, option, value):
        """Parse a configuration string into kwargs for `__init__`."""
        if value is None:
            arguments = []
        else:
            arguments = value.split()

        # Let base class do its thing
        kwargs = Task.parse_config(section, option, value)

        if option.startswith('replicate(') and option.endswith(')') :
            # Remove replicate instruction
            option = option[10:-1]
        else:
            raise RuntimeError("Bad replication task: %s"(option,))

        # Special case: basename == @
        # Get all entries in the directory and replace the @ with the (lexigraphically) last one
        dirname, basename = posixpath.split(option)
        if basename == '@':
            entries = list(t2kdm.utils.strip_output(t2kdm.ls(dirname, _iter=True)))
            entries.sort()
            option = posixpath.join(dirname, entries[-1])

        kwargs['destination'] = section
        kwargs['path'] = option

        return kwargs

    def _do(self):
        for line in t2kdm.replicate(self.path, self.destination, recursive=True, _iter=True):
            print_(line, end='')
    def __str__(self):
        """Return a string to identify the task by."""

class TaskLog(object):
    """Class to handle the logging of task activity."""

    def __init__(self, filename):
        """Use the given filename as log file."""
        self.filename = filename
        self.timeformat = "%Y-%m-%d_%H:%M:%S%z"
        self.id = os.getpid() # Store an ID to identifiy different processes

    def timestamp(self):
        """Return the current timestamp."""
        time = datetime.now(utc)
        return time.strftime(self.timeformat)

    def log(self, state, task, id=None, end='\n'):
        """Log the STARTED, DONE or FAILED of a task.

        Prepends a timestamp and PID.
        """

        if state not in ['STARTED', 'DONE', 'FAILED']:
            return ValueError("Not a valid task state: %s"%(state,))

        if id is None:
            id = self.id
        with open(self.filename, 'at') as f:
            f.write("%s %s %s %s%s"%(self.timestamp(), id, state, task, end))

    class ParseError(Exception):
        pass

    def parse_time(self, timestamp):
        """Return a datetime object according to the timestamp."""
        timeformat = self.timeformat[:-2] # Need to remove '%z' because pthon <3.2 does not understand it
        timestamp = timestamp[:-5] # Same for timestamp, remove '+0000'
        # We just have to assume here that everything is in UTC
        try:
            dt = datetime.strptime(timestamp, timeformat)
        except ValueError:
            raise TaskLog.ParseError()

        # Make timezone-aware
        dt = dt.replace(tzinfo = utc)
        return dt

    def _parse_line(self, line):
        """Return dict of parsed line."""
        elements = line.split()
        if len(elements) != 4:
            raise TaskLog.ParseError()

        ret = {
            'time': self.parse_time(elements[0]),
            'id': elements[1],
            'state': elements[2],
            'task': elements[3],
        }
        return ret

    def parse_log(self):
        """Parse the log file and find the last STARTED, DONE and FAILED times of tasks."""

        last_started = {}
        last_done = {}
        last_failed = {}

        with open(self.filename, 'rt') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#'):
                    # Ignore comments
                    continue

                ret = self._parse_line(line)
                try: # to parse the line for task run information
                    ret = self._parse_line(line)
                except TaskLog.ParseError:
                    continue

                task = ret['task']
                state = ret['state']
                time = ret['time']
                id = ret['id']

                if state == 'STARTED':
                    # Started a task
                    if task not in last_started or time > last_started[task][1]: # Entry is a tuple of (id, time)
                        last_started[task] = (id, time)
                elif state == 'DONE':
                    # Started a task
                    if task not in last_done or time > last_done[task][1]: # Entry is a tuple of (id, time)
                        last_done[task] = (id, time)
                elif state == 'FAILED':
                    # Started a task
                    if task not in last_failed or time > last_failed[task][1]: # Entry is a tuple of (id, time)
                        last_failed[task] = (id, time)

        return last_started, last_done, last_failed

class Maid(object):
    """Class that deals with all regular data keeping tasks.

    It takes care of replication, checksum checks, and regular reports of the state of things.
    """

    def __init__(self, configfile):
        """Initialise the Maid with the given configuration file.

        The file must look like this:

            [DEFAULT]
            tasklog = /path/to/logfile.txt

            [SOME_SE_NAME]
            replicate(/first/folder/to/be/replicated/to/the/SE) = daily
            replicate(/second/folder/to/be/replicated/to/the/SE) = daily
            replicate(/etc/)

            [SOME_OTHER_SE_NAME]
            replicate(/first/folder/to/be/replicated/to/the/SE) = weekly
            replicate(/second/different/folder/to/be/replicated/to/the/SE/@) = daily
            replicate(/etc) = monthly

            [ETC]
            replicate(/etc) = recursive daily

        Optionally, arguments can be assigned to the paths.
        Available arguments:

            daily           - aim to replicate this path every day
            weekly          - aim to replicate this path about once per week (default)
            monthly         - aim to replicate this path about once per month

        Each replication order is handled independently. So it is possible to
        request a weekly transfer of a certain folder, while additionally replicating
        a subfolder every day:

            [EXAMPLE_SE]
            replicate(/some/data/folder) = weekly
            replicate(/some/data/folder/very/important/@) = daily

        If the last level of the path to be replicated is an '@' character,
        it will be replaced by the *lexigraphically* last element in the directory.
        This can be used to define a daily replica of the newest data when the
        folder structure is updated with the run numbers.

        """

        parser = configparser.SafeConfigParser(allow_no_value=True)
        parser.optionxform = str # Need to make options case sensitive
        parser.read(configfile)

        self.tasklog = TaskLog(parser.get('DEFAULT', 'tasklog'))

        self.tasks = {}
        for sec in parser.sections():
            if sec in t2kdm.storage.SE_by_name:
                print_("Reading tasks for %s..."%(sec,))
                for opt in parser.options(sec):
                    if opt.startswith('replicate'):
                        val = parser.get(sec, opt) # Create the task from the config file line
                        if val is None:
                            print_("Adding replication task: %s"%(opt,))
                        else:
                            print_("Adding replication task: %s = %s"%(opt,val))
                        new_task = ReplicationTask(**ReplicationTask.parse_config(sec, opt, val))
                        new_id = new_task.get_id()
                        if new_id in self.tasks: # Make sure the task does not already exist
                            raise RuntimeError("Duplicate task: %s"%(new_id,))

                        # Store task in dict of tasks
                        self.tasks[new_id] = new_task

    def do_task(self, task):
        """Do a specific task and log it in the tasklog.

        Return `True` if succesfull.
        """
        self.tasklog.log('STARTED', task.get_id())
        try:
            task.do()
        except:
            self.tasklog.log('FAILED', task.get_id())
            success = False
        else:
            self.tasklog.log('DONE', task.get_id())
            success = True

        return success

    def update_task_states(self):
        """Read log and update when tasks were last done."""

        started, done, failed = self.tasklog.parse_log()

        for task in started:
            if task in self.tasks:
                t = self.tasks[task]
                t.last_done = started[task][1] # tuple of (pid, time)
                t.last_id =  started[task][0]
                t.state = 'STARTED'

        for task in done:
            if task in self.tasks:
                t = self.tasks[task]
                if t.last_done is None:
                    t.last_done = done[task][1] # tuple of (pid, time)
                    t.last_id =  done[task][0]
                    t.state = 'DONE'
                elif task in started and started[task][1] < done[task][1]:
                    # Do not overwrite the last time the task was started
                    t.last_id =  done[task][0]
                    t.state = 'DONE'

        for task in failed:
            if task in self.tasks:
                t = self.tasks[task]
                if t.last_done is None:
                    t.last_done = failed[task][1] # tuple of (pid, time)
                    t.last_id =  failed[task][0]
                    t.state = 'FAILED'
                elif task in started and started[task][1] < failed[task][1]:
                    # Do not overwrite the last time the task was started
                    t.last_id =  failed[task][0]
                    t.state = 'FAILED'

    def get_open_tasks(self, return_all=False):
        """Return a list of open tasks in order of how due they are.

        If `return_all` is `True`, all tasks will be returned, not just the due ones.
        """

        self.update_task_states()

        ret = []
        for t in self.tasks:
            task = self.tasks[t]
            if return_all or task.get_due() >= 0:
                ret.append(task)

        # Sort tasks by dueness
        ret.sort(key=lambda tsk: tsk.get_due(), reverse=True) # Highest due on top
        return ret

    def do_something(self, eager=False):
        """Find an open task and do it.

        If `eager` is `True`, do tasks even before they are due again.
        """
        tasks = self.get_open_tasks(return_all=eager)

        if len(tasks) > 0:
            print_("Due tasks:")
            for t in tasks:
                print_("* %s (%.3f)"%(t, t.get_due()))

            for t in tasks:
                if t.state == 'STARTED' and pid_running(int(t.last_id)):
                    print_("%s seems to be running already. Skipping..."%(t,))
                    continue
                else:
                    # Found a task we should do
                    break
            else:
                print_("All due tasks seem to be running already. Nothing to do.")
                return

            print_("Starting %s..."%(t))
            if self.do_task(tasks[0]):
                print_("Done.")
            else:
                print_("Failed.")
        else:
            print_("Nothing to do.")

def run_maid():
    """Start the Maid program and do some tasks.

    Intended to be run multiple times per day, but at least daily.
    """

    parser = argparse.ArgumentParser(description="Regular housekeeping for the T2K data. Run at least daily!")
    parser.add_argument('-e', '--eager', action='store_true',
                        help="do a task, even if it is not due yet")
    args = parser.parse_args()

    maid = Maid(t2kdm.config.maid_config)
    maid.do_something(eager=args.eager)

if __name__ == '__main__':
    run_maid()