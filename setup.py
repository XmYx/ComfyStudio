from setuptools import setup, find_packages

setup(
    name='comfystudio',
    version='0.1',
    description='Cinema Shot Designer for ComfyUI',
    author='Your Name',
    package_dir={"": "src"},  # Look for packages in the src directory
    packages=find_packages(where="src"),  # Automatically find packages under src
    install_requires=[
        'PyQt6<=6.7.1',
        'PyQt6-Qt6<=6.7.3',
        'PyQt6_sip<=13.8.0',
        'requests',
        'qtpy',
        'opencv-contrib-python',
        'av'
    ],
    entry_points={
        'console_scripts': [
            'comfystudio = comfystudio.main:main'
        ]
    }
)