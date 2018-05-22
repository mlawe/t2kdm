"""Module handling the command line interface (CLI)."""

from six import print_
import cmd
import sh
"""T2K Data Manager Command Line Interface (CLI)

The CLI makes it possible to comfortably browse the grid files. All commands
that are exposed as stand-alone scripts are also available in the CLI. This is
ensured by registering the Commands in the `all_commands` list in the
`commands` module.
"""

import shlex
import argparse
import os
from os import path
import posixpath
import t2kdm
from t2kdm.commands import all_commands

class T2KDmCli(cmd.Cmd):
    intro = """Welcome to the T2K Data Manager CLI.
  ____  ___   _  _  ____  __  __       ___  __    ____
 (_  _)(__ \ ( )/ )(  _ \(  \/  )___  / __)(  )  (_  _)
   )(   / _/ |   (  )(_) ))    ((___)( (__  )(__  _)(_
  (__) (____)(_)\_)(____/(_/\/\_)     \___)(____)(____)

Type 'help' or '?' to list commands.
"""
    prompt = '(t2kdm) '

    def __init__(self, *args, **kwargs):
        cmd.Cmd.__init__(self, *args, **kwargs)

        # Current directories for relative paths
        self.remotedir = posixpath.abspath('/')
        self.localdir = path.abspath(os.getcwd())

    def do_pwd(self, arg):
        """usage: pwd

        Print the current remote directory.
        """
        print_(self.remotedir)

    def do_lpwd(self, arg):
        """usage: lpwd

        Print the current local directory.
        """
        print_(self.localdir)

    def get_abs_remote_path(self, arg):
        """Return absolute remote path."""
        if posixpath.isabs(arg):
            return arg
        else:
            return posixpath.normpath(posixpath.join(self.remotedir, arg))

    def get_abs_local_path(self, arg):
        """Return absolute local path."""
        if path.isabs(arg):
            return arg
        else:
            return path.normpath(path.join(self.localdir, arg))

    def do_cd(self, arg):
        """usage: cd remotepath

        Change the current remote diretory.

        Note: Currently there are no checks done whether the remote directory actually exists.
        """
        pwd = self.get_abs_remote_path(arg)
        try: # Let us see whether the path is a directory
            t2kdm.ls(pwd)
        except sh.ErrorReturnCode_1 as e:
            print_("ERROR, no such remote directory: %s"%(pwd,))
        else:
            self.remotedir = pwd

    def do_lcd(self, arg):
        """usage: cd localpath

        Change the current local diretory.
        """
        pwd = self.get_abs_local_path(arg)
        if path.isdir(pwd):
            try:
                os.chdir(pwd)
            except OSError as e: # Catch permission errors
                print_(e)
            self.localdir = path.abspath(os.getcwd())
        else:
            print_("ERROR, no such local directory: %s"%(pwd,))

    def do_lls(self, arg):
        """usage: lls [-l] localpath

        List contents of local directory.
        """
        try:
            argv = shlex.split(arg)
        except ValueError as e: # Catch errors from bad bash syntax
            print_(e)
            return False

        try:
            print_(sh.ls('-1', *argv, _bg_exc=False), end='')
        except sh.ErrorReturnCode as e:
            print_(e.stderr, end='')

    def do_exit(self, arg):
        """Exit the CLI."""
        return True

    def completedefault(self, text, line, begidx, endidx):
        """Complete with content of current remote or local dir."""

        candidates = []

        # The built-in argument parsing is not very good.
        # Let's try our own.
        try:
            args = shlex.split(line)
        except ValueError: # Catch badly formatted strings
            args = line.split()

        if len(args) == 1:
            # Just the main command
            # Text should be empty
            search_text = ''
        else:
            search_text = args[-1]

        text_offset = len(search_text) - len(text)

        # Local commands start with 'l'.
        # Special case 'ls'
        if line[0] == 'l' and line[1] != 's':
            # Local path
            # Get contents of dir
            for l in sh.ls(self.localdir, '-1', _iter=True):
                l = l.strip()
                if l.startswith(search_text):
                    candidates.append(l[text_offset:])
        else:
            # Remote path
            # Get contents of dir
            for l in t2kdm.ls(self.remotedir, _iter=True):
                l = l.strip()
                if l.startswith(search_text):
                    candidates.append(l[text_offset:])

        return candidates

# Load all commands into the CLI
# Each `do_X` method in the class is interpreted as a possible command for the CLI.
# Each `help_X` method in the class is called when `help X` is executed.
for command in all_commands:
    do_name = 'do_'+command.name
    def do_cmd(cli, arg): # Since this is a method, the first argument will be the CLI instance
        return command.run_from_cli(arg, localdir=cli.localdir, remotedir=cli.remotedir)
    setattr(T2KDmCli, do_name, do_cmd) # Set the `do_X` attribute of the class

    help_name = 'help_'+command.name
    def help_cmd(cli): # Since this is a method, the first argument will be the CLI instance
        return command.run_from_cli('-h')
    setattr(T2KDmCli, help_name, help_cmd) # Set the `help_X` attribute of the class

def run_cli():
    try:
        T2KDmCli().cmdloop()
    except KeyboardInterrupt: # Exit gracefully on CTRL-C
        pass

if __name__ == '__main__':
    run_cli()
