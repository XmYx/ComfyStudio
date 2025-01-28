#!/usr/bin/env python
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback

import requests

from qtpy.QtCore import QRunnable

from qtpy.QtCore import (
    QObject,
    Signal,
    Slot
)


class RenderWorkerSignals(QObject):
    """Signals for the RenderWorker."""
    finished = Signal()            # Emits when the worker finishes
    error = Signal(str)            # Emits on error
    result = Signal(dict, int, bool)  # Emits the result data, shotIndex, isVideo

class RenderWorker(QRunnable):
    """
    A worker that sends a workflow JSON to Comfy, obtains prompt_id,
    and polls for results. Once the result is obtained or an error
    occurs, it emits signals.
    """
    def __init__(self, workflow_json, shotIndex, isVideo, comfy_ip, parent=None):
        super().__init__()
        self.signals = RenderWorkerSignals()
        self.workflow_json = workflow_json
        self.shotIndex = shotIndex
        self.isVideo = isVideo
        self.comfy_ip = comfy_ip.rstrip("/")
        self._stop = False
        self.parent = parent

    @Slot()
    def run(self):
        """Run the worker: send prompt, wait for necessary files, poll for results, emit signals."""
        try:
            # Wait for any parameters that depend on previous results to have valid file paths
            params = self.workflow_json.get("parameters", {}).get("params", [])
            for param in params:
                if param.get("usePrevResultImage") or param.get("usePrevResultVideo"):
                    file_path = param.get("value", "")
                    if not os.path.exists(file_path):
                        logging.debug(f"Waiting for file {file_path} to exist for parameter '{param['name']}'")
                        timeout = 300  # seconds
                        interval = 1   # seconds
                        elapsed = 0
                        while not os.path.exists(file_path):
                            if self._stop:
                                logging.debug("RenderWorker stopped while waiting for file.")
                                self.signals.finished.emit()
                                return
                            time.sleep(interval)
                            elapsed += interval
                            if elapsed >= timeout:
                                self.signals.error.emit(f"Timeout waiting for file: {file_path}")
                                self.signals.finished.emit()
                                return
                        logging.debug(f"File {file_path} exists. Proceeding with rendering.")

            prompt_id = self.sendPrompt()
            if not prompt_id:
                self.signals.error.emit("No prompt_id returned from ComfyUI.")
                self.signals.finished.emit()
                return

            # Poll for results
            while not self._stop:
                url = f"{self.comfy_ip}/history/{prompt_id}"
                try:
                    resp = requests.get(url)
                    if resp.status_code == 200:
                        rd = resp.json()
                        if rd:
                            self.signals.result.emit(rd, self.shotIndex, self.isVideo)
                            break
                    elif resp.status_code == 404:
                        pass
                except Exception as e:
                    self.signals.error.emit(str(e))
                    break
                time.sleep(2)  # Poll every 2 seconds
        finally:
            self.signals.finished.emit()

    def stop(self):
        """Stop polling."""
        self._stop = True

    def sendPrompt(self):
        """Send the prompt to ComfyUI, return prompt_id or None."""
        url = f"{self.comfy_ip}/prompt"
        headers = {"Content-Type": "application/json"}
        data = {"prompt": self.workflow_json}
        try:
            r = requests.post(url, headers=headers, json=data)
            r.raise_for_status()
            js = r.json()
            pid = js.get("prompt_id", None)
            return pid
        except Exception as e:
            logging.error(f"Failed to send prompt to ComfyUI: {e}")
            return None


class CustomNodesSetupWorker(QObject):
    log_message = Signal(str)
    finished = Signal()

    def __init__(self, config_file: str, venv_path: str, comfy_exec_path: str):
        super().__init__()
        self.config_file = config_file
        self.venv_path = venv_path
        self.comfy_exec_path = comfy_exec_path

    @Slot()
    def run(self):
        """
        Executes the custom nodes setup:
        - Clones or updates Git repositories.
        - Installs dependencies via install.py or requirements.txt.
        """
        # try:
        # Read the configuration file
        if not os.path.exists(self.config_file):
            self.log_message.emit(f"Config file not found: {self.config_file}")
            self.finished.emit()
            return

        with open(self.config_file, 'r') as f:
            config = json.load(f)
        self.log_message.emit("Configuration file loaded successfully.")

        # Extract the list of custom node repositories
        custom_nodes_repos = config.get("custom_nodes", [])
        if not custom_nodes_repos:
            self.log_message.emit("No custom_nodes repositories found in config.")
            self.finished.emit()
            return

        # Determine the custom_nodes directory based on ComfyUI's executable path
        comfy_dir = os.path.dirname(self.comfy_exec_path)
        custom_nodes_dir = os.path.join(comfy_dir, "custom_nodes")
        os.makedirs(custom_nodes_dir, exist_ok=True)
        self.log_message.emit(f"Custom nodes directory ensured at: {custom_nodes_dir}")

        # Determine paths to the virtual environment's Python and pip executables
        if sys.platform == "win32":
            venv_python = os.path.join(self.venv_path, "Scripts", "python.exe")
            venv_pip = os.path.join(self.venv_path, "Scripts", "pip.exe")
        else:
            venv_python = os.path.join(self.venv_path, "bin", "python")
            venv_pip = os.path.join(self.venv_path, "bin", "pip")

        # Verify the existence of Python and pip in the virtual environment
        if not os.path.exists(venv_python):
            self.log_message.emit(f"Python executable not found in venv: {venv_python}")
            self.finished.emit()
            return
        if not os.path.exists(venv_pip):
            self.log_message.emit(f"Pip executable not found in venv: {venv_pip}")
            self.finished.emit()
            return

        self.log_message.emit("Virtual environment's Python and pip verified.")

        # Iterate over each repository URL
        for repo_url in custom_nodes_repos:
            repo_name = repo_url.rstrip('/').split('/')[-1]
            if repo_name.endswith('.git'):
                repo_name = repo_name[:-4]  # Remove .git suffix if present
            target_path = os.path.join(custom_nodes_dir, repo_name)

            if os.path.isdir(target_path):
                # Check if the directory is a Git repository
                git_dir = os.path.join(target_path, '.git')
                if os.path.isdir(git_dir):
                    # Update the repository by pulling the latest changes
                    self.log_message.emit(f"Updating repository: {repo_name}")
                    try:
                        result = subprocess.run(
                            ["git", "-C", target_path, "pull"],
                            check=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True
                        )
                        self.log_message.emit(result.stdout)
                        self.log_message.emit(f"Updated {repo_name} successfully.")
                    except subprocess.CalledProcessError as e:
                        self.log_message.emit(f"Failed to update {repo_name}: {e.stderr}")
                        continue
                else:
                    self.log_message.emit(f"Directory {target_path} exists but is not a git repository. Skipping.")
                    continue
            else:
                # Clone the repository since it doesn't exist
                self.log_message.emit(f"Cloning repository: {repo_name}")
                try:
                    result = subprocess.run(
                        ["git", "clone", repo_url, target_path],
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    self.log_message.emit(result.stdout)
                    self.log_message.emit(f"Cloned {repo_name} successfully.")
                except subprocess.CalledProcessError as e:
                    self.log_message.emit(f"Failed to clone {repo_name}: {e.stderr}")
                    continue

            # After cloning/updating, handle dependency installation
            install_py = os.path.join(target_path, "install.py")
            requirements_txt = os.path.join(target_path, "requirements.txt")

            if os.path.isfile(install_py):
                self.log_message.emit(f"Running install.py for {repo_name}")
                try:
                    result = subprocess.run(
                        [venv_python, "install.py"],
                        check=True,
                        cwd=target_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    self.log_message.emit(result.stdout)
                    self.log_message.emit(f"install.py for {repo_name} executed successfully.")
                except subprocess.CalledProcessError as e:
                    self.log_message.emit(f"Failed to run install.py for {repo_name}: {e.stderr}")
            elif os.path.isfile(requirements_txt):
                self.log_message.emit(f"Installing requirements for {repo_name}")
                try:
                    result = subprocess.run(
                        [venv_pip, "install", "-r", "requirements.txt"],
                        check=True,
                        cwd=target_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    self.log_message.emit(result.stdout)
                    self.log_message.emit(f"Requirements for {repo_name} installed successfully.")
                except subprocess.CalledProcessError as e:
                    self.log_message.emit(f"Failed to install requirements for {repo_name}: {e.stderr}")
            else:
                self.log_message.emit(f"No install.py or requirements.txt found for {repo_name}.")

        self.log_message.emit("Custom nodes setup completed.")
        self.finished.emit()
class ComfyWorker(QObject):
    log_message = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, py_path: str, main_path: str):
        super().__init__()
        self.py_path = py_path
        self.main_path = main_path
        self.process = None
        self._is_running = True

    @Slot()
    def run(self):
        """
        Launches the ComfyUI process and reads its stdout and stderr.
        Emits log messages and handles process termination.
        """
        try:
            self.log_message.emit("Starting ComfyUI process...")
            self.process = subprocess.Popen(
                [self.py_path, self.main_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            self.log_message.emit("ComfyUI process started.")

            # Start threads to read stdout and stderr
            stdout_thread = threading.Thread(target=self.read_stream, args=(self.process.stdout, False))
            stderr_thread = threading.Thread(target=self.read_stream, args=(self.process.stderr, True))
            stdout_thread.start()
            stderr_thread.start()

            # Wait for the process to complete
            self.process.wait()

            # Wait for threads to finish
            stdout_thread.join()
            stderr_thread.join()

            if self._is_running:
                self.log_message.emit("ComfyUI process finished.")
            else:
                self.log_message.emit("ComfyUI process terminated by user.")

            self.finished.emit()
        except Exception as e:
            error_trace = traceback.format_exc()
            self.error.emit(f"Exception in ComfyWorker: {str(e)}\n{error_trace}")
            self.finished.emit()

    def read_stream(self, stream, is_stderr):
        """
        Reads a stream (stdout or stderr) line by line and emits log messages.
        """
        try:
            for line in iter(stream.readline, ''):
                if not self._is_running:
                    break
                prefix = "STDERR" if is_stderr else "STDOUT"
                self.log_message.emit(f"[{prefix}] {line.strip()}")
        except Exception as e:
            self.error.emit(f"Error reading stream: {str(e)}")
        finally:
            stream.close()

    def stop(self):
        """
        Terminates the ComfyUI process gracefully.
        """
        self._is_running = False
        if self.process and self.process.poll() is None:
            self.log_message.emit("Terminating ComfyUI process...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
                self.log_message.emit("ComfyUI process terminated.")
            except subprocess.TimeoutExpired:
                self.log_message.emit("ComfyUI process did not terminate gracefully. Killing process...")
                self.process.kill()
                self.log_message.emit("ComfyUI process killed.")
