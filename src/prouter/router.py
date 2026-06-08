import inspect
import os
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pandas as pd

# Metadata/junk entries to skip wholesale (never descended into, never yielded).
_SKIP_NAMES = frozenset(
    {
        "Thumbs.db",  # Windows thumbnail cache
        "Desktop.ini",  # Windows folder metadata cache
        ".DS_Store",  # iOS/macOS Finder metadata cache
        ".AppleDouble",  # Apple metadata cache
        ".Spotlight-V100",
        ".Trashes",
        "__MACOSX",
    }
)


def bottom_up_traversal(path: Path) -> Iterator[Path]:
    """Yield every path under ``path`` from the leaves up to the root.

    Args:
        path: The directory root path to traverse.

    Yields:
        Each path in the tree, deepest first, ending with ``path``.

    Raises:
        TypeError: If ``path`` is not a ``Path``.

    Notes:
        Uses ``os.scandir`` so each directory entry's type is read once and
        reused, avoiding a separate ``stat`` per child -- a large saving on
        networked volumes. Symlinks are not followed, so symlink cycles cannot
        cause infinite recursion. Unreadable directories are yielded as leaves
        rather than raising.
    """
    if not isinstance(path, Path):
        raise TypeError(f"path must be a Path, got {type(path).__name__}")

    yield from _bottom_up(path)


def _bottom_up(path: Path) -> Iterator[Path]:
    """Recursive post-order worker for :func:`bottom_up_traversal`."""
    if path.name in _SKIP_NAMES:
        return

    try:
        scandir_it = os.scandir(path)
    except NotADirectoryError:
        # ``path`` is a file given as the traversal root: yield it as a leaf.
        yield path
        return
    except OSError:
        # Unreadable/vanished directory (permissions, network drop): yield it,
        # but don't descend.
        yield path
        return

    with scandir_it:
        for entry in scandir_it:
            if entry.name in _SKIP_NAMES:
                continue
            # ``entry.is_dir()`` is served from the cached type;
            # no extra syscall in the common case.
            if entry.is_dir(follow_symlinks=False):
                yield from _bottom_up(Path(entry.path))
            else:
                yield Path(entry.path)
    yield path


def validate_uniqueness_and_disjointness(patterns: list[re.Pattern]) -> bool:
    """Validate that each pattern is unique and that the input and output pattern sets are disjoint.

    Args:
        patterns: List of compiled regex patterns.

    Returns:
        True if validation passes, otherwise False.
    """
    pattern_set = set(p.pattern for p in patterns)

    # Check for duplicates within patterns
    if len(pattern_set) != len(patterns):
        return False

    return True


def find_candidates(patterns: list[re.Pattern], path: Path) -> list[re.Pattern]:
    """Match the base of a path against the patterns and report the match list.

    Args:
        patterns: List of compiled regex patterns to match against basename.
        path: The path who's basename is to be matched against the patterns.

    Returns:
        A list of patterns that fullmatch the basename of the path, or an empty list if no patterns match.
    """
    matching_patterns = []  # Populated with patterns matching the basename of the path

    # Check every pattern against the basename for fullmatch
    for pattern in patterns:
        if pattern.fullmatch(path.name):
            matching_patterns.append(pattern)

    return matching_patterns


class GraphBuilder:
    """Builder for the graph of mappings from path -> input pattern -> node -> output pattern -> new path
    with validation of output and input validation inferrable."""

    def __init__(self, root_path: Path, results_folder: Path) -> None:
        """Initialize the GraphBuilder with the necessary components to build the graph."""

        self.root_path = root_path  # All paths beneath the root pass through the graph
        self.results_folder = results_folder  # Where to save the graph CSVs
        self.router = {}  # Mapping input pattern -> (input_pattern, node, output_pattern)
        self.graph = []  # List of tuples (path, input_pattern, node, output_pattern, new_path)
        self.input_patterns = []  # Faster lookup and validation of input patterns
        self.output_patterns = []  # Faster lookup and validation of output patterns
        self.pattern_names = {}  # Compiled pattern -> variable name harvested at add_route time

        if not self.root_path.exists():
            raise ValueError(f"Path '{self.root_path}' does not exist.")
        if not self.results_folder.exists():
            raise ValueError(f"Path '{self.results_folder}' does not exist.")
        if not self.results_folder.is_dir():
            raise ValueError(f"Path '{self.results_folder}' is not a directory.")
        for filename in [
            "routable_paths.csv",
            "problem_paths.csv",
            "clean_paths.csv",
            "routes.csv",
        ]:
            if (self.results_folder / filename).exists():
                raise ValueError(
                    f"File '{filename}' already exists in '{self.results_folder}'. "
                    "Please remove it to avoid overwriting."
                )

        print("Initialized GraphBuilder")

    @staticmethod
    def _harvest_name(pattern: re.Pattern) -> str:
        """Find the variable name bound to ``pattern`` in the caller of ``add_route``.

        Walks back two frames (past ``add_route``) to inspect the caller's local
        and global namespaces. Returns the first matching variable name, or the
        regex source if the pattern was passed as an unnamed literal.
        """
        caller = inspect.currentframe().f_back.f_back
        if caller is not None:
            for namespace in (caller.f_locals, caller.f_globals):
                for name, value in namespace.items():
                    if value is pattern:
                        return name
        return pattern.pattern

    def _label(self, pattern: re.Pattern) -> str:
        """Return the harvested variable name for ``pattern``, else its regex source."""
        return self.pattern_names.get(pattern, pattern.pattern)

    @staticmethod
    def _format_eta(elapsed: float, completed: int, total: int) -> str:
        if completed <= 0 or total <= 0:
            return "unknown"
        rate = elapsed / completed
        remaining = max(total - completed, 0) * rate
        return f"{remaining:.1f}s"

    def add_route(self, input_pattern: re.Pattern, node: Callable, output_pattern: re.Pattern) -> None:
        """add routes for how to transform paths when pattern is met."""
        # Check if route is already present
        if input_pattern in self.router:
            raise ValueError(f"Route for input pattern '{input_pattern.pattern}' already exists.")

        # Add input to the list of input patterns
        self.input_patterns.append(input_pattern)

        # Add output to the list of output patterns if not already present
        if output_pattern not in self.output_patterns:
            self.output_patterns.append(output_pattern)

        # Validate uniqueness and disjointness of patterns
        if not validate_uniqueness_and_disjointness(self.input_patterns + self.output_patterns):
            self.input_patterns.pop()  # Remove the last added input pattern
            self.output_patterns.pop()  # Remove the last added output pattern
            raise ValueError(f"Input pattern '{input_pattern.pattern}' is not unique.")

        # Harvest the variable names the caller used, for human-readable CSV output
        self.pattern_names.setdefault(input_pattern, self._harvest_name(input_pattern))
        self.pattern_names.setdefault(output_pattern, self._harvest_name(output_pattern))

        # Set route for quick lookup during graph building
        self.router[input_pattern] = (input_pattern, node, output_pattern)

    def build(self) -> dict[Path, list[tuple[re.Pattern, Callable, re.Pattern]]]:
        """Build the graph by traversing the directory tree and asserting patterns match.

        Returns:
            A list of tuples (path, input_pattern, node, output_pattern, new_path, valid) for each
            matching route and result.
        """
        # Walking a (possibly networked) tree can take a while with no feedback,
        # so report discovery progress as paths stream in.
        discovery_start = time.perf_counter()
        paths = []
        for path in bottom_up_traversal(self.root_path):
            paths.append(path)
            print(
                f"\rDiscovering paths: {len(paths)} found ({time.perf_counter() - discovery_start:.1f}s)",
                end="",
                flush=True,
            )
        print()  # Finish the discovery line
        total = len(paths)
        start = time.perf_counter()

        for index, path in enumerate(paths, start=1):
            elapsed = time.perf_counter() - start
            eta = self._format_eta(elapsed, index - 1, total)
            print(f"\rBuilding graph: {index}/{total} ({(index / total * 100):.1f}%) - ETA {eta}", end="", flush=True)

            candidates = find_candidates(self.input_patterns, path)
            if candidates:
                if len(candidates) > 1:
                    raise ValueError(
                        f"Ambiguous path '{path}' matches multiple patterns: {[p.pattern for p in candidates]}"
                    )
                for candidate in candidates:  # Skips if candidates is empty
                    valid = False
                    input_pattern, node, output_pattern = self.router[candidate]
                    new_path = node(path)
                    # A node may only rewrite the basename; it must never move a
                    # path between directories. Anything but the basename changing
                    # is a misbehaving node, not a routing result.
                    if new_path.parent != path.parent:
                        raise ValueError(
                            f"Node '{node.__name__}' changed more than the basename of '{path}': "
                            f"parent '{path.parent}' != '{new_path.parent}'. "
                            "Nodes may only rewrite the basename."
                        )
                    if output_pattern.fullmatch(new_path.name):
                        valid = True
                    self.graph.append((path, input_pattern, node, output_pattern, new_path, valid))
            else:
                self.graph.append((path, "", "", "", "", ""))

        print()  # Finish the progress line
        print(f"Graph built with {len(self.graph)} entries in {(time.perf_counter() - start):.1f}s")
        return self.graph

    def save(self) -> None:
        """Save the graph to CSV files at the specified folder."""
        path = self.results_folder
        if not self.router:
            print("No routes to save. Please add routes before saving.")
            return
        if not self.graph:
            print("No graph to save. Please run build() before saving.")
            return

        # Double Check for output collisons to see if we can't save before processing the graph (unlikely case)
        if not path.exists():
            raise ValueError(f"Path '{path}' does not exist.")
        if not path.is_dir():
            raise ValueError(f"Path '{path}' is not a directory.")
        for filename in [
            "routable_paths.csv",
            "problem_paths.csv",
            "clean_paths.csv",
            "routes.csv",
        ]:
            if (path / filename).exists():
                raise ValueError(
                    f"File '{filename}' already exists in '{path}'. Please remove it to avoid overwriting."
                )

        # Exactly one input_pattern is expected to match each path.
        routable_paths = {"path": [], "node": [], "input_pattern": [], "output_pattern": [], "new_path": []}

        # Paths that have more than one candidate pattern match are ambiguous and said to be not routable.
        problem_paths = {"path": [], "node": [], "input_pattern": [], "output_pattern": [], "new_path": []}

        # Paths that have no candidate pattern are ignored and not routable.
        clean_paths = {"path": [], "node": [], "input_pattern": [], "output_pattern": [], "new_path": []}

        total = len(self.graph)
        start = time.perf_counter()

        for index, (old_path, input_pattern, node, output_pattern, new_path, valid) in enumerate(self.graph, start=1):
            elapsed = time.perf_counter() - start
            eta = self._format_eta(elapsed, index - 1, total)
            print(f"\rSaving graph: {index}/{total} ({(index / total * 100):.1f}%) - ETA {eta}", end="", flush=True)

            if input_pattern and output_pattern:
                if valid:
                    routable_paths["path"].append(str(old_path))
                    routable_paths["node"].append(node.__name__)
                    routable_paths["input_pattern"].append(self._label(input_pattern))
                    routable_paths["output_pattern"].append(self._label(output_pattern))
                    routable_paths["new_path"].append(str(new_path))
                else:
                    problem_paths["path"].append(str(old_path))
                    problem_paths["node"].append(node.__name__)
                    problem_paths["input_pattern"].append(self._label(input_pattern))
                    problem_paths["output_pattern"].append(self._label(output_pattern))
                    problem_paths["new_path"].append(str(new_path))
            else:
                clean_paths["path"].append(str(old_path))
                clean_paths["node"].append("")
                clean_paths["input_pattern"].append("")
                clean_paths["output_pattern"].append("")
                clean_paths["new_path"].append("")

        print()  # Finish the progress line
        print(f"Graph saved to {path} in {(time.perf_counter() - start):.1f}s")
        print(f"  Routable paths: {len(routable_paths['path'])}")
        print(f"  Problem paths: {len(problem_paths['path'])}")
        print(f"  Clean paths: {len(clean_paths['path'])}")
        pd.DataFrame(routable_paths).to_csv(path / "routable_paths.csv", index=False)
        pd.DataFrame(problem_paths).to_csv(path / "problem_paths.csv", index=False)
        pd.DataFrame(clean_paths).to_csv(path / "clean_paths.csv", index=False)

        # The set of configured routes, independent of any matched paths.
        routes = {"input_pattern": [], "node": [], "output_pattern": []}
        for input_pattern, node, output_pattern in self.router.values():
            routes["input_pattern"].append(self._label(input_pattern))
            routes["node"].append(node.__name__)
            routes["output_pattern"].append(self._label(output_pattern))
        pd.DataFrame(routes).to_csv(path / "routes.csv", index=False)
        print(f"  Routes: {len(routes['input_pattern'])}")

        print(f"Saved graph to {path}")
