from setuptools import setup, find_packages

setup(
    name="hdhomerun-screamrouter-plugin",
    version="0.1.0",
    description="HDHomeRun plugin for ScreamRouter with auto-discovery",
    author="netmindzD",
    py_modules=["hdhomerun_plugin"],
    install_requires=[
        "requests>=2.31.0",
        "zeroconf>=0.131.0",
    ],
    python_requires=">=3.8",
)
