from setuptools import setup

setup(
    name='socklocks',
    version='0.1.0',
    description='Library of Python locks that use sockets to keep processes '
                'synchronized.',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    classifiers=(
        'Programming Language :: Python',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Environment :: Web Environment',
    ),
    keywords=('query string', 'querystring', 'URL', 'parser'),
    author='Justin Turner Arthur',
    author_email='justinarthur@gmail.com',
    url='https://github.com/JustinTArthur/socklocks',
    license='Apache License 2.0',
    py_modules=('socklocks',)
)
