# prouter

[![PyPI](https://img.shields.io/pypi/v/prouter)](https://pypi.org/project/prouter)
[![WiscLab](https://img.shields.io/badge/WiscLab-kidspeech.wisc.edu-c5050c)](https://kidspeech.wisc.edu/)
![ShredGuard](https://img.shields.io/badge/ShredGuard-ON-06B6D4?logo=git&logoColor=white&style=flat-square)

[![CI](https://github.com/WISCLab/prouter/actions/workflows/ci.yml/badge.svg)](https://github.com/WISCLab/prouter/actions/workflows/ci.yml)
[![CD](https://github.com/WISCLab/prouter/actions/workflows/cd.yml/badge.svg)](https://github.com/WISCLab/prouter/actions/workflows/cd.yml)

Route filesystem paths through `pattern -> handler -> pattern` rules with visibility.

The idea is simple. You define routes, each one an input pattern, a handler that rewrites a path, and an output pattern the result has to match. prouter walks a directory tree and runs every path through the route whose input pattern matches its basename, keeping track of what it did along the way.

It never touches the filesystem. Nothing gets moved or renamed. What you get back is a set of CSVs describing the transform it would apply, so you can look it over before committing to anything.

The tree is walked bottom-up, deepest paths first and the root last. This is deliberate: renaming children before their parents means a directory rename never invalidates paths you haven't reached yet. The CSVs preserve that order, so applying the rows top to bottom is always safe.

If the route-building syntax feels familiar, that's on purpose: it's inspired by LangGraph's way of wiring up nodes.

## Install

```bash
pip install prouter
```

Requires Python 3.12+.

## Usage

```python
import re
from pathlib import Path
from prouter import GraphBuilder

# An input pattern, a handler (Path -> Path), and the output pattern the result must match.
draft = re.compile(r"(\d+)_draft\.wav")
final = re.compile(r"\d+_final\.wav")

def rename(path: Path) -> Path:
    return path.with_name(path.name.replace("_draft", "_final"))

builder = GraphBuilder(root_path=Path("/path/to/draw/from"), results_folder=Path("/Folder/to/save/results"))
builder.add_route(draft, rename, final)

builder.build()             # walk the tree, match routes, apply handlers in memory
builder.save()              # write the result CSVs
```

> [!NOTE]
> When adding a route with `add_route` You can't register the same input pattern twice (a ValueError raises). Matching against those patterns is a `fullmatch` against the whole basename, so a pattern has to account for the entire filename, not just part of it. And at build() time, only one input pattern may match a given path. If two would match, that's ambiguous and prouter raises rather than guess.

## Output

`save()` drops four CSVs into the folder you give it:

- `routable_paths.csv` — matched a route, and the handler's output lined up with the output pattern.
- `problem_paths.csv` — matched a route, but the handler's output did **not** match the output pattern. These are the ones to look at.
- `clean_paths.csv` — matched nothing, left alone.
- `routes.csv` — the routes you configured, on their own.

The path CSVs share the same columns: `path`, `node`, `input_pattern`, `output_pattern`, `new_path`. `routes.csv` just has `input_pattern`, `node`, `output_pattern`.

> [!NOTE]
> In those columns, things show up under the names you gave them. Handlers use the function's `__name__`, so `rename` lands in the CSV as `rename`. Patterns are trickier, since a compiled regex has no name of its own, so prouter peeks at the calling frame and recovers the variable you bound it to: `draft` and `final` above come out as `draft` and `final`. Pass a bare `re.compile(...)` inline with no variable and it falls back to the raw regex source.
