from setuptools import setup, find_packages

setup(
    name='qcodes-measurements',
    version='0.1',
    description='QNL qcodes measurement procedures',
    url='https://github.com/QNLSydney/qcodes-measurements',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Science/Research',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Licence :: MIT Licence',
        'Topic :: Scientific/Engineering'
    ],
    license='MIT',
    packages=find_packages(),
    install_requires=[
        'matplotlib>=2.0.2',
        'pyqtgraph>=0.10.0',
        'qcodes>=0.8.0',
        'wrapt>=1.10.11',
        'PyQt5>=5.12.2',
        'scipy>=1.1.0',
        'numpy>=1.14.3',
        'tabulate>=0.8.3',
        'tqdm>=4.41.1',
        'requests>=2.22.0'
    ],
    python_requires='>=3'
)
