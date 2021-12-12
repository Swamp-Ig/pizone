"""Setup package"""

from setuptools import setup, find_packages  # type: ignore

with open("README.md", "r") as fh:
    LONG_DESCRIPTION = fh.read()

setup(
    name="python-izone",
    version="1.2.2",
    author="Penny Wood",
    author_email="pypl@ninjateaparty.com",
    description="A python interface to the iZone airconditioner controller",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    keywords=['iZone', 'IoT', ],
    url="https://github.com/Swamp-Ig/pizone",
    python_requires='~=3.8',
    install_requires=['aiohttp>=3.4', 'netifaces'],
    tests_require=['pytest-aio'],
    packages=find_packages(exclude=['tests', 'tests.*']),
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.8",
        "License :: OSI Approved :: "
            "GNU General Public License v3 or later (GPLv3+)",
        "Operating System :: OS Independent",
        "Topic :: Home Automation",
        "Topic :: System :: Hardware"
    ],
)
