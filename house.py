#!/usr/bin/python

import os
import shlex
import signal
import subprocess
import sys
import time
from threading import Thread
try:
	from Queue import Queue, Empty
except ImportError:
	from queue import Queue, Empty


_SECURE_DEFAULT = False

class SandboxError(Exception):
	pass

if sys.version_info >= (3,):
	def unicode(s, errors="strict"):
		if isinstance(s, str):
			return s
		elif isinstance(s, bytes) or isinstance(s, bytearray):
			return s.decode("utf-8", errors)
		raise SandboxError("Tried to convert unrecognized type to unicode")

def _monitor_file(fd, q):
	while True:
		line = fd.readline()
		if not line:
			q.put(None)
			break
		line = unicode(line, errors="replace")
		line = line.rstrip('\r\n')
		q.put(line)

class House:
	"""Provide an insecure sandbox to run arbitrary commands in.

	The sandbox class is used to invoke arbitrary shell commands.
	This class provides the same interface as the secure Sandbox but doesn't
	provide any actual security or require any special system setup.

	"""

	def __init__(self, working_directory):
		"""Initialize a new sandbox for the given working directory.

		working_directory: the directory in which the shell command should
						   be launched.
		"""
		self._is_alive = False
		self.command_process = None
		self.stdout_queue = Queue()
		self.stderr_queue = Queue()
		self.working_directory = working_directory

	@property
	def is_alive(self):
		"""Indicates whether a command is currently running in the sandbox"""
		if self._is_alive:
			sub_result = self.command_process.poll()
			if sub_result is None:
				return True
			self.child_queue.put(None)
			self._is_alive = False
		return False

	def start(self, shell_command):
		"""Start a command running in the sandbox"""
		if self.is_alive:
			raise SandboxError("Tried to run command with one in progress.")
		working_directory = self.working_directory
		self.child_queue = Queue()
		shell_command = shlex.split(shell_command.replace('\\','/'))
		try:
			self.command_process = subprocess.Popen(shell_command,
													stdin=subprocess.PIPE,
													stdout=subprocess.PIPE,
													stderr=subprocess.PIPE,
													universal_newlines=True,
													cwd=working_directory)
		except OSError:
			raise SandboxError('Failed to start {0}'.format(shell_command))
		self._is_alive = True
		stdout_monitor = Thread(target=_monitor_file,
								args=(self.command_process.stdout, self.stdout_queue))
		stdout_monitor.daemon = True
		stdout_monitor.start()
		stderr_monitor = Thread(target=_monitor_file,
								args=(self.command_process.stderr, self.stderr_queue))
		stderr_monitor.daemon = True
		stderr_monitor.start()
		Thread(target=self._child_writer).start()

	def kill(self):
		"""Stops the sandbox.

		Shuts down the sandbox, cleaning up any spawned processes, threads, and
		other resources. The shell command running inside the sandbox may be
		suddenly terminated.

		"""
		if self.is_alive:
			try:
				self.command_process.kill()
			except OSError:
				pass
			self.command_process.wait()
			self.child_queue.put(None)

	def retrieve(self):
		"""Copy the working directory back out of the sandbox."""
		if self.is_alive:
			raise SandboxError("Tried to retrieve sandbox while still alive")
		pass

	def release(self):
		"""Release the sandbox for further use

		If running in a jail unlocks and releases the jail for reuse by others.
		Must be called exactly once after Sandbox.kill has been called.

		"""
		if self.is_alive:
			raise SandboxError("Sandbox released while still alive")
		pass

	def pause(self):
		"""Pause the process by sending a SIGSTOP to the child

		A limitation of the method is it will only pause the initial
		child process created any further (grandchild) processes created
		will not be paused.

		This method is a no-op on Windows.
		"""
		try:
			self.command_process.send_signal(signal.SIGSTOP)
		except (ValueError, AttributeError, OSError):
			pass

	def resume(self):
		"""Resume the process by sending a SIGCONT to the child

		This method is a no-op on Windows
		"""
		try:
			self.command_process.send_signal(signal.SIGCONT)
		except (ValueError, AttributeError, OSError):
			pass

	def _child_writer(self):
		queue = self.child_queue
		stdin = self.command_process.stdin
		while True:
			ln = queue.get()
			if ln is None:
				break
			try:
				stdin.write(ln)
				stdin.flush()
			except (OSError, IOError):
				self.kill()
				break

	def write(self, str):
		"""Write str to stdin of the process being run"""
		if not self.is_alive:
			return False
		self.child_queue.put(str)

	def write_line(self, line):
		"""Write line to stdin of the process being run

		A newline is appended to line and written to stdin of the child process

		"""
		if not self.is_alive:
			return False
		self.child_queue.put(line + "\n")

	def read_line(self, timeout=0):
		"""Read line from child process

		Returns a line of the child process' stdout, if one isn't available
		within timeout seconds it returns None. Also guaranteed to return None
		at least once after each command that is run in the sandbox.

		"""
		if not self.is_alive:
			timeout=0
		try:
			return self.stdout_queue.get(block=True, timeout=timeout)
		except Empty:
			return None

	def read_error(self, timeout=0):
		"""Read line from child process' stderr

		Returns a line of the child process' stderr, if one isn't available
		within timeout seconds it returns None. Also guaranteed to return None
		at least once after each command that is run in the sandbox.

		"""
		if not self.is_alive:
			timeout=0
		try:
			return self.stderr_queue.get(block=True, timeout=timeout)
		except Empty:
			return None

	def check_path(self, path, errors):
		resolved_path = os.path.join(self.working_directory, path)
		if not os.path.exists(resolved_path):
			errors.append("Output file " + str(path) + " was not created.")
			return False
		else:
			return True

def get_sandbox(working_dir, secure=None):
	if secure is None:
		secure = _SECURE_DEFAULT
	if secure:
		return Jail(working_dir)
	else:
		return House(working_dir)
