# Zim–Zotero Integration

A set of plugins for integrating [Zim Desktop Wiki](https://zim-wiki.org/) with [Zotero](https://www.zotero.org/).

The project currently consists of three components:

* **Zotero Citations** — integration with Zotero for working with bibliographic/material records and searching the Zotero library.
* **Zotero Fragments** — working with Material and Fragment records created from Zotero references.
* **Zotero Local API** — a shared Python module used by the Zim plugins to communicate with the local Zotero API.

The plugins are intended for users who keep their bibliographic information in Zotero and use Zim as a working environment for notes, citations, and fragments.

## Current status

This repository contains a working development version prepared for external testing.

The main functionality has been tested during development, including different types of Zotero records. The project is still experimental and may contain bugs or require adjustments for different Zim/Zotero configurations.

Feedback and bug reports are welcome.

## Components

### Zotero Citations

Provides integration between Zim and Zotero for bibliographic records and citation-related work.

The plugin includes Zotero search and processing of material records. It is intended to be used in the notebook configured as the Zotero citations/bibliography notebook.

### Zotero Fragments

Provides functionality for working with Fragments derived from Zotero material records.

The Fragment-related commands are intended for use in the working notebooks rather than the bibliography notebook.

### Zotero Local API

A small shared module providing access to Zotero's local API.

It is not a Zim plugin by itself. Both Zim plugins use this module.

---

# Installation

## Requirements

Before installing the plugins, make sure that you have:

* Linux
* Zim Desktop Wiki
* Zotero
* Zotero running with its local API available

The plugins are currently intended for testing with the Linux version of Zim and Zotero.

## 1. Download the repository

Clone the repository:

```bash
git clone https://github.com/USERNAME/REPOSITORY.git
```

or download the repository as a ZIP archive and extract it.

The repository contains the following directories:

```text
zotero-zim/
├── zotero_citations/
│   └── __init__.py
├── zotero_fragments/
│   └── __init__.py
└── zotero_local_api/
    └── zotero_local_api.py
```

## 2. Install the Zim plugins

Copy the `zotero_citations` and `zotero_fragments` directories to your Zim user plugins directory.

For a standard Linux installation, this is:

```text
~/.local/share/zim/plugins/
```

After installation, the directory structure should look like:

```text
~/.local/share/zim/plugins/
├── zotero_citations/
│   └── __init__.py
└── zotero_fragments/
    └── __init__.py
```

If the `plugins` directory does not exist, create it:

```bash
mkdir -p ~/.local/share/zim/plugins
```

## 3. Install the shared Zotero API module

The `zotero_local_api` directory is installed separately from the Zim plugins.

Create the required directory:

```bash
mkdir -p ~/.local/share/zim/zotero_local_api
```

Copy the module:

```text
zotero_local_api/zotero_local_api.py
```

to:

```text
~/.local/share/zim/zotero_local_api/zotero_local_api.py
```

The final structure should therefore be:

```text
~/.local/share/zim/
├── plugins/
│   ├── zotero_citations/
│   │   └── __init__.py
│   └── zotero_fragments/
│       └── __init__.py
└── zotero_local_api/
    └── zotero_local_api.py
```

`Zotero Citations` adds this directory to Python's module search path automatically, so no manual `PYTHONPATH` configuration is required.

## 4. Restart Zim

Close all running Zim windows and start Zim again.

Open:

**Edit → Preferences → Plugins**

and enable:

* **Zotero Citations**
* **Zotero Fragments**

The plugins should then become available in Zim.

## 5. Configure the plugins

The plugins use their Zim plugin preferences for the connection between the Zotero bibliography notebook and the working notebooks.

The first run may create local configuration files under:

```text
~/.config/zim/zotero/
```

These files are user-specific and should not be copied between installations or committed to the Git repository.

## 6. Start Zotero

Zotero must be running when the plugins need to access the Zotero library.

The plugins communicate with the local Zotero API at:

```text
http://127.0.0.1:23119/api/
```

No remote Zotero server is required for this communication.

---

# Testing

After installation, first verify that:

1. Zim starts without errors.
2. Both plugins appear in the Zim plugin list.
3. **Zotero Search...** is available in the configured Zotero citations notebook.
4. Fragment-related commands are available in the appropriate working notebook.
5. Zotero is running and its local API can be accessed.
6. Zotero records can be found and processed.
7. Material and Fragment pages are created or used as expected.

Because this is an experimental testing version, please report any errors together with:

* Zim version;
* Zotero version;
* Linux distribution;
* the type of Zotero record being processed;
* the exact operation that caused the problem;
* the error message, if one is displayed.
