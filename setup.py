from setuptools import setup, find_packages

setup(
    name='comfystudio',
    version='0.1',
    description='Cinema Shot Designer for ComfyUI',
    author='Your Name',
    package_dir={"": "src"},  # Look for packages in the src directory
    packages=find_packages(where="src"),  # Automatically find packages under src
    install_requires=[
        # Qt >= 6.8: QtWebEngine 6.6's Chromium segfaults on macOS 26
        # (dock-layout hide of the render widget after a page load).
        'PyQt6>=6.8',
        'PyQt6-Qt6>=6.8',
        'PyQt6-WebEngine>=6.8',
        'PyQt6_WebEngine_Qt6>=6.8',
        'PyQt6_sip>=13.8',
        'requests',
        'qtpy',
        'opencv-contrib-python',
        'av',
        'huggingface-hub',
        'bs4'
    ],
    entry_points={
        'console_scripts': [
            'comfystudio = comfystudio.main:main'
        ]
    }
)
