# ComfyStudio

ComfyStudio is a powerful and extensible application designed for cinematic shot design and video processing. Built with Python and Qt, it provides an intuitive interface for creating, managing, and rendering shots using ComfyUI workflows. It supports a plugin system for additional features, custom export options, and integration with various workflows.

## Features

- **Shot Management:** Create, duplicate, delete, extend shots using a drag-and-drop interface. Easily import shots from text files with custom parameter mapping.

- **Parameter Control:** Set and adjust global and shot-specific parameters for image and video workflows. Parameters include basic types, image/video selectors, and custom settings.

- **Workflow Integration:** Select and run image and video workflows directly from the UI. Render stills and videos with customizable parameters, monitor progress, and automatically handle ComfyUI responses.

- **Video Preview:** Built-in video preview and media controls (play, pause, stop) for immediate feedback on rendered video outputs.

- **Export Functionality:** Export projects as individual clips or merge into a single video using customizable FFmpeg commands. Supports codec selection, custom arguments, and output destination configuration.

- **Shot Wizard:** Generate shots using LLM (Large Language Model) workflows. Interactively edit prompts, iterate for multiple outputs, and automatically create shots from generated text lines.

- **Extensibility:** Plugin system for adding new features. The application dynamically loads plugins from the `plugins` directory, allowing developers to extend functionality without modifying core code.

- **Settings Management:** Save and load user preferences such as ComfyUI paths, IP configuration, and default parameters. Persistent project saving and loading with JSON format.

- **Cross-Platform Launch Scripts:** Includes `start.bat` and `start.sh` scripts to initialize a virtual environment and run the application on Windows and Unix-like systems.

## Moddability and Customization

- **Plugin System:** Easily add new features by creating Python plugins in the `plugins` directory. Plugins can register new actions, modify workflows, and integrate with the main application.

- **Workflow Editing:** Use the built-in Workflow Editor to modify and create custom ComfyUI workflows. Workflows are stored in JSON format and can be extended or customized to fit specific needs.

- **Configurable Parameters:** Define default shot, image, and video parameters. Change global settings via the Settings dialog to tailor the rendering process and application behavior.

- **Open Source:** The repository is available on GitHub, allowing contributions, bug fixes, and feature requests. The codebase is structured for ease of understanding and extension.

## Installation

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/XmYx/ComfyStudio.git
   cd ComfyStudio
   ```

2. **Setup Virtual Environment:**
   - On Windows:
     ```bash
     start.bat
     ```
   - On Unix/Linux/Mac:
     ```bash
     ./start.sh
     ```

3. **Run Application:**
   The provided scripts will create a virtual environment, install dependencies, and launch ComfyStudio.

## Repository Structure

- `main.py`: Entry point of the application.
- `plugins/`: Directory for additional plugins.
- `workflows/`: Contains image, video, and LLM workflows for rendering and shot generation.
- `defaults/`: Default configuration files.
- `start.bat` and `start.sh`: Scripts to start the application in a virtual environment.
- Other modules provide UI components, settings management, rendering logic, and ComfyUI integration.

## Contributing

Contributions are welcome! Feel free to open issues, submit pull requests, or propose new plugins and workflows. For major changes, please open an issue to discuss what you would like to change.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Acknowledgments

Thanks to the open source community and contributors for their support and valuable feedback.