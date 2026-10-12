from setuptools import setup, find_packages

setup(
    name="ollambo",
    version="1.0.0",
    description="Your local autonomous AI coding companion & chat assistant — powered by Ollama",
    author="Ollambo Team",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "PySide6>=6.8.0",
        "httpx>=0.27.0",
        "platformdirs>=4.0.0",
    ],
    entry_points={
        "console_scripts": [
            "ollambo=main:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Code Generators",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
)
