|logo|

.. |logo| image:: https://raw.githubusercontent.com/scrapy/scrapy/master/docs/_static/logo.svg
   :target: https://scrapy.org
   :alt: Scrapy
   :width: 480px

|version| |python_version| |ubuntu| |macos| |windows| |coverage| |conda| |deepwiki|

.. |version| image:: https://img.shields.io/pypi/v/Scrapy.svg
   :target: https://pypi.org/pypi/Scrapy
   :alt: PyPI Version

.. |python_version| image:: https://img.shields.io/pypi/pyversions/Scrapy.svg
   :target: https://pypi.org/pypi/Scrapy
   :alt: Supported Python Versions

.. |ubuntu| image:: https://github.com/scrapy/scrapy/workflows/Ubuntu/badge.svg
   :target: https://github.com/scrapy/scrapy/actions?query=workflow%3AUbuntu
   :alt: Ubuntu

.. |macos| image:: https://github.com/scrapy/scrapy/workflows/macOS/badge.svg
   :target: https://github.com/scrapy/scrapy/actions?query=workflow%3AmacOS
   :alt: macOS

.. |windows| image:: https://github.com/scrapy/scrapy/workflows/Windows/badge.svg
   :target: https://github.com/scrapy/scrapy/actions?query=workflow%3AWindows
   :alt: Windows

.. |coverage| image:: https://img.shields.io/codecov/c/github/scrapy/scrapy/master.svg
   :target: https://codecov.io/github/scrapy/scrapy?branch=master
   :alt: Coverage report

.. |conda| image:: https://anaconda.org/conda-forge/scrapy/badges/version.svg
   :target: https://anaconda.org/conda-forge/scrapy
   :alt: Conda Version

.. |deepwiki| image:: https://deepwiki.com/badge.svg
   :target: https://deepwiki.com/scrapy/scrapy
   :alt: Ask DeepWiki

Scrapy_ is a web scraping framework to extract structured data from websites.
It is cross-platform, and requires Python 3.9+. It is maintained by Zyte_
(formerly Scrapinghub) and `many other contributors`_.

.. _many other contributors: https://github.com/scrapy/scrapy/graphs/contributors
.. _Scrapy: https://scrapy.org/
.. _Zyte: https://www.zyte.com/

Install with:

.. code:: bash

    pip install scrapy

And follow the documentation_ to learn how to use it.

.. _documentation: https://docs.scrapy.org/en/latest/

If you wish to contribute, see Contributing_.

.. _Contributing: https://docs.scrapy.org/en/master/contributing.html

Running with Docker
-------------------

You can build a container image that bundles Scrapy together with the
``extras/link_contact_extractor.py`` helper script:

.. code:: bash

    docker build -t scrapy-toolkit .

Once built, the image exposes the Scrapy command-line interface by default,
so you can, for example, open an interactive shell against a site:

.. code:: bash

    docker run --rm -it scrapy-toolkit shell https://inisheng.com --nolog

To execute the JSON link/contact extractor from the container, override the
entry point and pass the target URL:

.. code:: bash

    docker run --rm -it --entrypoint python scrapy-toolkit \
        extras/link_contact_extractor.py https://inisheng.com

Add ``-s USER_AGENT="..."`` to the Scrapy command if the target site requires
a custom user agent.

.. note::

   The image ships the Scrapy framework itself and the helper scripts in
   ``extras/``, but it does not include a sample Scrapy project. Commands such
   as ``scrapy crawl <spider>`` must be executed from within a Scrapy project
   directory (one that contains a ``scrapy.cfg`` file), for example by mounting
   your own project into the container and using ``-w`` to set the working
   directory.

Expose the scanning API
~~~~~~~~~~~~~~~~~~~~~~~

The project also includes an HTTP API that can fan out concurrent requests,
allowing you to scan large batches of domains (100+ per minute on a typical
VPS). Launch the service directly on your machine with ``uvicorn``:

.. code:: bash

    uvicorn extras.link_contact_api:app --host 0.0.0.0 --port 8000

Or run it inside the Docker image and publish the port to your host:

.. code:: bash

    docker run --rm -it -p 8000:8000 scrapy-toolkit \
        uvicorn extras.link_contact_api:app --host 0.0.0.0 --port 8000

Send a POST request with a list of URLs to ``/scan`` to trigger the crawl:

.. code:: bash

    curl -X POST http://localhost:8000/scan \
      -H 'Content-Type: application/json' \
      -d '{"urls": ["https://example.com", "https://docs.scrapy.org"], "concurrency": 32}'

The response contains a ``summary`` describing how many domains were scanned
successfully alongside the full per-domain breakdown.

Offline self-test
~~~~~~~~~~~~~~~~~

If you are working in an environment without outbound network access you can
still verify that the helper utilities behave as expected. The repository
includes a small HTML fixture and a self-test script that exercises the link
and contact extraction logic without making any HTTP requests:

.. code:: bash

   python extras/link_contact_selftest.py --pretty

The script prints the expected and observed results in JSON format and exits
with a non-zero status if a regression is detected. You can also supply your
own HTML fixture::

   python extras/link_contact_selftest.py path/to/page.html
