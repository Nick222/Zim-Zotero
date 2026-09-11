## How Zotero, the Citation Notebook and the Working Notebook are connected

The plugin is designed to work with **three related components**:

1. **Zotero database** — the source of bibliographic information and Material records.
2. **Citation Notebook in Zim** — a Zim notebook that represents and organizes the materials imported from Zotero.
3. **Working Notebook in Zim** — the notebook where the actual work with the materials takes place, including inserting and using Fragments.

The relationship can be represented as:

**Zotero → Citation Notebook → Working Notebook**

### 1. Zotero database

Zotero remains the **source of bibliographic information**.

The plugin communicates with Zotero through its local API and obtains information about Zotero records from there. The bibliographic data is therefore not entered manually into Zim.

### 2. Citation Notebook

The **Citation Notebook** is the Zim notebook used to work with the bibliographic/material records obtained from Zotero.

It provides the Zim-side representation of the materials in Zotero and is the place where operations such as **Zotero Search** are performed.

The Citation Notebook is therefore not a replacement for the Zotero database. It is the Zim-side working representation of the Zotero materials.

### 3. Working Notebook

The **Working Notebook** is where the user actually uses the materials in their own notes.

Fragments selected from the materials can be inserted into pages of the Working Notebook. The Working Notebook therefore contains the user's working text and references to material extracted from Zotero records.

### Why are there two Zim notebooks?

The separation is intentional.

The **Citation Notebook** is concerned with the bibliographic/material side of the workflow:

**Zotero records → materials in Zim**

The **Working Notebook** is concerned with using those materials:

**materials → fragments → user's notes**

This separation also determines which plugin commands are available in each notebook. Commands related to searching and managing Zotero materials belong to the Citation Notebook, while commands for working with already selected Materials and Fragments belong to the Working Notebook.

### Typical workflow

A typical workflow looks like this:

1. A bibliographic record exists in **Zotero**.
2. The plugin reads the record from Zotero.
3. The corresponding material is created or processed in the **Citation Notebook**.
4. The user works with that material and its Fragments.
5. A selected Fragment can be inserted into a page in the **Working Notebook**.
6. The bibliographic information continues to originate from Zotero rather than being independently edited in the Working Notebook.

The three components therefore have different roles:

| Component             | Role                                                  |
| --------------------- | ----------------------------------------------------- |
| **Zotero**            | Source of bibliographic/material information          |
| **Citation Notebook** | Zim representation and management of Zotero materials |
| **Working Notebook**  | User's actual working notes and use of Fragments      |

The plugin is designed around this separation rather than treating the three as interchangeable storage locations.
