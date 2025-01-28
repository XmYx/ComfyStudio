#!/usr/bin/env python
import json
import logging
import os
import subprocess
import sys

from qtpy.QtCore import (
    QObject,
    Signal,
    Slot,
    QThread
)
from qtpy.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QFileDialog,
    QPushButton,
    QLabel,
    QComboBox,
    QMessageBox,
    QWizard,
    QWizardPage,
    QRadioButton,
    QButtonGroup,
    QProgressBar
)


class EmittingStream(QObject):
    text_written = Signal(str)

    def write(self, text):
        self.text_written.emit(str(text))

    def flush(self):
        pass


class QtLogHandler(logging.Handler):
    def __init__(self, emit_stream):
        super().__init__()
        self.emit_stream = emit_stream

    def emit(self, record):
        log_entry = self.format(record)
        self.emit_stream.write(log_entry + '\n')


class ComfyInstallerWizard(QWizard):
    """
    A wizard to install or update ComfyUI and its dependencies.
    Steps:
        1. Select Python Environment
        2. Select ComfyUI Installation Directory
        3. Clone ComfyUI Repository
        4. Install Torch Based on GPU Architecture
        5. Install ComfyUI Dependencies
    """

    def __init__(self, parent=None, settings_manager=None, log_callback=None):
        super().__init__(parent)
        self.setWindowTitle("ComfyUI Installer Wizard")
        self.setWizardStyle(QWizard.ModernStyle)

        self.settings_manager = settings_manager
        self.log_callback = log_callback  # Function to append logs to UI

        # Initialize variables to store user selections
        self.selected_env_path = ""
        self.comfyui_install_dir = ""
        self.git_clone_success = False
        self.torch_install_success = False
        self.dependencies_install_success = False

        # Add wizard pages
        self.addPage(EnvSelectionPage())
        self.addPage(ComfyUIInstallPage())
        self.addPage(CloningPage())
        self.addPage(TorchInstallPage())
        self.addPage(DependenciesInstallPage())

        # Connect page transitions if needed
        self.currentIdChanged.connect(self.onPageChanged)

    def onPageChanged(self, current_id):
        """
        Handle actions when the current page changes.
        """
        if current_id == 2:  # CloningPage
            cloning_page = self.page(2)
            cloning_page.startCloning(self.comfyui_install_dir, self.log_callback)
        elif current_id == 3:  # TorchInstallPage
            torch_page = self.page(3)
            torch_page.install_torch(self.selected_env_path, self.log_callback)
        elif current_id == 4:  # DependenciesInstallPage
            deps_page = self.page(4)
            deps_page.install_dependencies(self.selected_env_path, self.comfyui_install_dir, self.log_callback)

    def accept(self):
        """
        Override accept to perform final actions after the wizard is completed.
        """
        # After successful installation, set the ComfyUI main.py path in settings
        main_py_path = os.path.join(self.comfyui_install_dir, "main.py")
        if os.path.isfile(main_py_path):
            self.settings_manager.set("comfy_main_path", main_py_path)
            self.settings_manager.set("comfy_py_path", self.selected_env_path)
            self.settings_manager.save()
            QMessageBox.information(self, "Success", "ComfyUI has been installed/updated successfully.")
            super().accept()
        else:
            QMessageBox.warning(self, "Error", "main.py not found in the installation directory. Installation may have failed.")
            super().reject()


class EnvSelectionPage(QWizardPage):
    """
    Page 1: Select Python Environment
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Select Python Environment")
        self.setSubTitle("Choose an existing Python environment or specify a custom path.")

        layout = QVBoxLayout()

        self.env_combo = QComboBox()
        self.env_combo.setToolTip("Select a Python environment to use for ComfyUI.")
        self.refresh_envs()

        self.refresh_btn = QPushButton("Refresh Environments")
        self.refresh_btn.setToolTip("Refresh the list of available Python environments.")
        self.refresh_btn.clicked.connect(self.refresh_envs)

        self.custom_path_edit = QLineEdit()
        self.custom_path_edit.setPlaceholderText("Enter custom Python executable path...")
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setToolTip("Browse to select a custom Python executable.")
        self.browse_btn.clicked.connect(self.browse_custom_path)

        custom_layout = QHBoxLayout()
        custom_layout.addWidget(self.custom_path_edit)
        custom_layout.addWidget(self.browse_btn)

        layout.addWidget(QLabel("Available Python Environments:"))
        layout.addWidget(self.env_combo)
        layout.addWidget(self.refresh_btn)
        layout.addSpacing(20)
        layout.addWidget(QLabel("Or specify a custom Python executable:"))
        layout.addLayout(custom_layout)

        self.setLayout(layout)

    def refresh_envs(self):
        """
        Populate the env_combo with available Python environments.
        For simplicity, this example searches common conda environments.
        """
        self.env_combo.clear()
        conda_envs = self.get_conda_envs()
        if conda_envs:
            for env in conda_envs:
                self.env_combo.addItem(env['name'], env['python'])
        else:
            self.env_combo.addItem("No conda environments found.")

    def get_conda_envs(self):
        """
        Retrieve a list of conda environments.
        """
        try:
            result = subprocess.run(
                ["conda", "env", "list", "--json"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            env_data = json.loads(result.stdout)
            envs = []
            for name, path in env_data.get("envs", []):
                python_executable = os.path.join(path, "python.exe" if sys.platform.startswith("win") else "bin/python")
                if os.path.isfile(python_executable):
                    envs.append({"name": name, "python": python_executable})
            return envs
        except Exception as e:
            return []

    def browse_custom_path(self):
        """
        Open a file dialog to select a custom Python executable.
        """
        options = QFileDialog.Options()
        file_filter = "Python Executable (python.exe python)"
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Python Executable", "", file_filter, options=options)
        if file_path:
            self.custom_path_edit.setText(file_path)

    def validatePage(self):
        """
        Validate user input before proceeding to the next page.
        """
        # Ensure that either an environment is selected or a custom path is provided
        selected_env = self.env_combo.currentData()
        custom_path = self.custom_path_edit.text().strip()

        if selected_env and os.path.isfile(selected_env):
            self.complete = True
            self.selected_python = selected_env
            return True
        elif custom_path and os.path.isfile(custom_path):
            self.complete = True
            self.selected_python = custom_path
            return True
        else:
            QMessageBox.warning(self, "Input Error", "Please select a valid Python environment or provide a valid Python executable path.")
            return False

    def get_selected_python(self):
        """
        Retrieve the selected Python executable path.
        """
        return getattr(self, 'selected_python', "")


class ComfyUIInstallPage(QWizardPage):
    """
    Page 2: Select ComfyUI Installation Directory
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Select ComfyUI Installation Directory")
        self.setSubTitle("Choose a directory where ComfyUI will be installed or updated.")

        layout = QVBoxLayout()

        self.install_dir_edit = QLineEdit()
        self.install_dir_edit.setPlaceholderText("Select installation directory...")
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setToolTip("Browse to select the installation directory for ComfyUI.")
        self.browse_btn.clicked.connect(self.browse_install_dir)

        install_layout = QHBoxLayout()
        install_layout.addWidget(self.install_dir_edit)
        install_layout.addWidget(self.browse_btn)

        layout.addWidget(QLabel("ComfyUI Installation Directory:"))
        layout.addLayout(install_layout)

        self.setLayout(layout)

    def browse_install_dir(self):
        """
        Open a directory selection dialog.
        """
        options = QFileDialog.Options()
        directory = QFileDialog.getExistingDirectory(self, "Select Installation Directory", "", options=options)
        if directory:
            self.install_dir_edit.setText(directory)

    def validatePage(self):
        """
        Validate the installation directory.
        """
        install_dir = self.install_dir_edit.text().strip()
        if not install_dir:
            QMessageBox.warning(self, "Input Error", "Please select an installation directory.")
            return False

        # Check if the directory exists or can be created
        if not os.path.exists(install_dir):
            try:
                os.makedirs(install_dir)
            except Exception as e:
                QMessageBox.warning(self, "Directory Error", f"Failed to create directory: {e}")
                return False

        self.selected_install_dir = install_dir
        return True

    def get_install_dir(self):
        """
        Retrieve the selected installation directory.
        """
        return getattr(self, 'selected_install_dir', "")


class CloningPage(QWizardPage):
    """
    Page 3: Clone ComfyUI Repository
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Clone ComfyUI Repository")
        self.setSubTitle("ComfyUI will be cloned into the selected installation directory.")

        layout = QVBoxLayout()

        self.status_label = QLabel("Status: Not started.")
        layout.addWidget(self.status_label)

        self.setLayout(layout)

    def startCloning(self, install_dir, log_callback):
        """
        Start cloning the ComfyUI repository.
        """
        repo_url = "https://github.com/comfyanonymous/ComfyUI.git"
        target_path = os.path.join(install_dir, "ComfyUI")

        if os.path.isdir(target_path):
            if os.path.isdir(os.path.join(target_path, ".git")):
                self.status_label.setText("Status: ComfyUI repository already exists. Pulling latest changes...")
                try:
                    result = subprocess.run(
                        ["git", "-C", target_path, "pull"],
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    self.status_label.setText("Status: Updated ComfyUI repository successfully.")
                    if log_callback:
                        log_callback(result.stdout)
                except subprocess.CalledProcessError as e:
                    self.status_label.setText("Status: Failed to update ComfyUI repository.")
                    if log_callback:
                        log_callback(e.stderr)
            else:
                self.status_label.setText("Status: Directory exists but is not a git repository. Skipping cloning.")
        else:
            self.status_label.setText("Status: Cloning ComfyUI repository...")
            try:
                result = subprocess.run(
                    ["git", "clone", repo_url, target_path],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                self.status_label.setText("Status: Cloned ComfyUI repository successfully.")
                if log_callback:
                    log_callback(result.stdout)
            except subprocess.CalledProcessError as e:
                self.status_label.setText("Status: Failed to clone ComfyUI repository.")
                if log_callback:
                    log_callback(e.stderr)

    def validatePage(self):
        """
        Proceed only if cloning was successful or already exists.
        """
        current_text = self.status_label.text()
        if "successfully" in current_text or "exists" in current_text:
            return True
        else:
            QMessageBox.warning(self, "Cloning Error", "Failed to clone or update the ComfyUI repository.")
            return False


class TorchInstallPage(QWizardPage):
    """
    Page 4: Install Torch Based on GPU Architecture
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Install PyTorch")
        self.setSubTitle("Select your GPU architecture to install the appropriate PyTorch version.")

        layout = QVBoxLayout()

        self.gpu_group = QButtonGroup(self)
        self.amd_radio = QRadioButton("AMD GPU (Linux only)")
        self.intel_native_radio = QRadioButton("Intel GPU (Native)")
        self.intel_extension_radio = QRadioButton("Intel GPU (IPEX)")
        self.nvidia_radio = QRadioButton("NVIDIA GPU")
        self.directml_radio = QRadioButton("DirectML (AMD on Windows)")
        self.ascend_radio = QRadioButton("Ascend NPU")
        self.apple_radio = QRadioButton("Apple Mac Silicon")
        self.other_radio = QRadioButton("Other / No GPU")

        self.gpu_group.addButton(self.amd_radio)
        self.gpu_group.addButton(self.intel_native_radio)
        self.gpu_group.addButton(self.intel_extension_radio)
        self.gpu_group.addButton(self.nvidia_radio)
        self.gpu_group.addButton(self.directml_radio)
        self.gpu_group.addButton(self.ascend_radio)
        self.gpu_group.addButton(self.apple_radio)
        self.gpu_group.addButton(self.other_radio)

        layout.addWidget(QLabel("Select your GPU architecture:"))
        layout.addWidget(self.amd_radio)
        layout.addWidget(self.intel_native_radio)
        layout.addWidget(self.intel_extension_radio)
        layout.addWidget(self.nvidia_radio)
        layout.addWidget(self.directml_radio)
        layout.addWidget(self.ascend_radio)
        layout.addWidget(self.apple_radio)
        layout.addWidget(self.other_radio)

        self.install_btn = QPushButton("Install PyTorch")
        self.install_btn.clicked.connect(self.on_install_clicked)
        layout.addWidget(self.install_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.setLayout(layout)

    def install_torch(self, python_executable, log_callback):
        """
        Placeholder for installing torch. Actual installation is triggered by the user clicking the install button.
        """
        pass

    def on_install_clicked(self):
        """
        Handle the install button click.
        """
        selected_button = self.gpu_group.checkedButton()
        if not selected_button:
            QMessageBox.warning(self, "Selection Error", "Please select a GPU architecture.")
            return

        selection = selected_button.text()
        if selection == "AMD GPU (Linux only)":
            cmd = [
                "pip",
                "install",
                "torch",
                "torchvision",
                "torchaudio",
                "--index-url",
                "https://download.pytorch.org/whl/rocm6.2"
            ]
            msg = "Installing PyTorch with ROCm 6.2 support..."
        elif selection == "Intel GPU (Native)":
            cmd = [
                "pip",
                "install",
                "--pre",
                "torch",
                "torchvision",
                "torchaudio",
                "--index-url",
                "https://download.pytorch.org/whl/nightly/xpu"
            ]
            msg = "Installing PyTorch Nightly with XPU support..."
        elif selection == "Intel GPU (IPEX)":
            cmd = [
                "conda",
                "install",
                "libuv",
                "-y"
            ]
            # After installing libuv, install IPEX
            # This will be handled in the subprocess
            msg = "Installing Intel Extension for PyTorch (IPEX)..."
        elif selection == "NVIDIA GPU":
            cmd = [
                "pip",
                "install",
                "torch",
                "torchvision",
                "torchaudio",
                "--extra-index-url",
                "https://download.pytorch.org/whl/cu124"
            ]
            msg = "Installing PyTorch with CUDA 12.4 support..."
        elif selection == "DirectML (AMD on Windows)":
            cmd = [
                "pip",
                "install",
                "torch-directml"
            ]
            msg = "Installing torch-directml for DirectML support..."
        elif selection == "Ascend NPU":
            QMessageBox.information(
                self,
                "Info",
                "Please refer to the Ascend NPU installation guide for detailed instructions."
            )
            return
        elif selection == "Apple Mac Silicon":
            cmd = [
                "pip",
                "install",
                "--pre",
                "torch",
                "torchvision",
                "torchaudio",
                "--index-url",
                "https://download.pytorch.org/whl/nightly/cpu"
            ]
            msg = "Installing PyTorch Nightly for Apple Mac Silicon..."
        elif selection == "Other / No GPU":
            cmd = [
                "pip",
                "install",
                "torch",
                "torchvision",
                "torchaudio"
            ]
            msg = "Installing PyTorch CPU version..."
        else:
            QMessageBox.warning(self, "Selection Error", "Unknown selection.")
            return

        # Disable the install button and show progress
        self.install_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        # Start the installation in a separate thread to keep UI responsive
        self.thread = QThread()
        self.worker = TorchInstallerWorker(cmd)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.log.connect(self.log_message)
        self.worker.finished.connect(self.on_install_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

        self.log_message(msg)

    @Slot(str)
    def log_message(self, message):
        """
        Log messages to the main application's log.
        """
        if self.parent():
            if self.parent().log_callback:
                self.parent().log_callback(message)
            else:
                print(message)

    @Slot()
    def on_install_finished(self):
        """
        Handle the completion of the torch installation.
        """
        self.progress_bar.setValue(100)
        QMessageBox.information(self, "Installation Complete", "PyTorch has been installed successfully.")
        self.install_btn.setEnabled(True)
        self.progress_bar.setVisible(False)


class TorchInstallerWorker(QObject):
    """
    Worker to install PyTorch in a separate thread.
    """
    progress = Signal(int)
    log = Signal(str)
    finished = Signal()

    def __init__(self, command):
        super().__init__()
        self.command = command

    def run(self):
        """
        Execute the installation command and emit progress and logs.
        """
        try:
            process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            for line in iter(process.stdout.readline, ''):
                if line:
                    self.log.emit(line.strip())
            process.stdout.close()
            return_code = process.wait()
            if return_code == 0:
                self.progress.emit(100)
                self.log.emit("PyTorch installation completed successfully.")
            else:
                self.log.emit(f"PyTorch installation failed with return code {return_code}.")
        except Exception as e:
            self.log.emit(f"An error occurred during PyTorch installation: {e}")
        finally:
            self.finished.emit()


class DependenciesInstallPage(QWizardPage):
    """
    Page 5: Install ComfyUI Dependencies
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Install ComfyUI Dependencies")
        self.setSubTitle("Install all required Python packages for ComfyUI.")

        layout = QVBoxLayout()

        self.status_label = QLabel("Status: Not started.")
        layout.addWidget(self.status_label)

        self.install_btn = QPushButton("Install Dependencies")
        self.install_btn.clicked.connect(self.on_install_clicked)
        layout.addWidget(self.install_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.setLayout(layout)

    def install_dependencies(self, python_executable, install_dir, log_callback):
        """
        Placeholder for installing dependencies. Actual installation is triggered by the user clicking the install button.
        """
        pass

    def on_install_clicked(self):
        """
        Handle the install button click.
        """
        # Retrieve the Python executable and ComfyUI install directory from previous pages
        wizard = self.wizard()
        env_page = wizard.page(0)
        install_page = wizard.page(1)

        python_executable = env_page.get_selected_python()
        install_dir = install_page.get_install_dir()
        requirements_path = os.path.join(install_dir, "ComfyUI", "requirements.txt")

        if not os.path.isfile(requirements_path):
            QMessageBox.warning(self, "File Error", f"requirements.txt not found in {install_dir}/ComfyUI.")
            return

        cmd = [
            python_executable,
            "-m",
            "pip",
            "install",
            "-r",
            requirements_path
        ]

        # Disable the install button and show progress
        self.install_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        # Start the installation in a separate thread to keep UI responsive
        self.thread = QThread()
        self.worker = DependenciesInstallerWorker(cmd)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.log.connect(self.log_message)
        self.worker.finished.connect(self.on_install_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

        self.log_message("Installing ComfyUI dependencies...")

    @Slot(str)
    def log_message(self, message):
        """
        Log messages to the main application's log.
        """
        if self.parent():
            if self.parent().log_callback:
                self.parent().log_callback(message)
            else:
                print(message)

    @Slot()
    def on_install_finished(self):
        """
        Handle the completion of the dependencies installation.
        """
        self.progress_bar.setValue(100)
        QMessageBox.information(self, "Installation Complete", "ComfyUI dependencies have been installed successfully.")
        self.install_btn.setEnabled(True)
        self.progress_bar.setVisible(False)


class DependenciesInstallerWorker(QObject):
    """
    Worker to install dependencies in a separate thread.
    """
    progress = Signal(int)
    log = Signal(str)
    finished = Signal()

    def __init__(self, command):
        super().__init__()
        self.command = command

    def run(self):
        """
        Execute the installation command and emit progress and logs.
        """
        try:
            process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            for line in iter(process.stdout.readline, ''):
                if line:
                    self.log.emit(line.strip())
            process.stdout.close()
            return_code = process.wait()
            if return_code == 0:
                self.progress.emit(100)
                self.log.emit("Dependencies installation completed successfully.")
            else:
                self.log.emit(f"Dependencies installation failed with return code {return_code}.")
        except Exception as e:
            self.log.emit(f"An error occurred during dependencies installation: {e}")
        finally:
            self.finished.emit()