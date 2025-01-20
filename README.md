# Cinema Shot Designer (Comfy Studio)

Cinema Shot Designer is a graphical application designed to manage and render cinematic shots using ComfyUI workflows. It allows users to create, modify, import, and render shots with various parameters for images and videos, organize shots in a project, and interact with ComfyUI for generating outputs. The application supports batch rendering, dynamic parameter selection, and a user-friendly interface built with PyQt6.

## Features
- Create new shots inheriting parameters from previous shots.
- Manage global and shot-specific parameters for images and videos.
- Render individual shots or batch render all stills/videos.
- Import shots from a TXT file, mapping lines to specific parameters.
- Toolbar for quick actions: Render All Stills, Render All Videos, and Stop Rendering.
- Right-click context menu on shots for duplicating or deleting.
- Save and load projects in JSON format.
- Configurable connection to a ComfyUI server via settings.

## Installation

1. Make sure you have Python 3.10 or higher installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Install the application:
   ```bash
   python setup.py install
   ```

## Usage

After installation, run the application using the command:
```bash
comfystudio
```

This will launch the Cinema Shot Designer GUI.

## License
This project is licensed under the MIT License.