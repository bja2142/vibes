from setuptools import find_packages, setup


setup(
    name="browser-puppet",
    version="0.1.0",
    description="Agentic browser-control MCP server for testing and evidence capture.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.11",
    install_requires=[
        "mcp>=1.10.0",
        "playwright>=1.52.0",
    ],
    extras_require={
        "dev": [
            "pytest>=8.2.0",
            "pytest-asyncio>=0.23.0",
        ]
    },
)
