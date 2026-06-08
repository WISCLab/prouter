"""Tests for GraphBuilder class and its methods."""

import re
import shutil
from pathlib import Path

import pandas as pd
import pytest

from prouter import GraphBuilder
from prouter.router import bottom_up_traversal


def make_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create an empty root/ and results/ pair under ``tmp_path``."""
    root = tmp_path / "root"
    root.mkdir()
    results = tmp_path / "results"
    results.mkdir()
    return root, results


class TestInitialization:
    def test_empty_root_path_works(self, tmp_path):
        # (1) Empty root_path should work
        root, results = make_dirs(tmp_path)
        gb = GraphBuilder(root, results)
        assert gb.root_path == root

    def test_empty_result_path_works(self, tmp_path):
        # (2) empty result_path should work
        root, results = make_dirs(tmp_path)
        (root / "a.txt").write_text("x")  # root populated, results left empty
        gb = GraphBuilder(root, results)
        assert gb.results_folder == results

    def test_nonexistent_root_raises(self, tmp_path):
        # (3) Nonexistent root_path should raise FileNotFoundError
        root = tmp_path / "missing"
        results = tmp_path / "results"
        results.mkdir()
        with pytest.raises(FileNotFoundError):
            GraphBuilder(root, results)

    def test_existing_result_file_raises(self, tmp_path):
        # (4) result_path with one of those files already in it should raise FileExistsError
        root, results = make_dirs(tmp_path)
        (results / "routes.csv").write_text("")
        with pytest.raises(FileExistsError):
            GraphBuilder(root, results)


class TestAddRoute:
    def test_duplicate_input_pattern_raises(self, tmp_path):
        # (1) If input pattern is already an input pattern for another route, raise ValueError
        root, results = make_dirs(tmp_path)
        gb = GraphBuilder(root, results)
        in_pat = re.compile(r"a\.txt")
        gb.add_route(in_pat, lambda p: p, re.compile(r"b\.txt"))
        with pytest.raises(ValueError):
            gb.add_route(in_pat, lambda p: p, re.compile(r"c\.txt"))

    def test_output_equals_existing_input_raises(self, tmp_path):
        # (2) If output pattern is already an input pattern for another route, raise ValueError
        root, results = make_dirs(tmp_path)
        gb = GraphBuilder(root, results)
        a = re.compile(r"a\.txt")
        b = re.compile(r"b\.txt")
        c = re.compile(r"c\.txt")
        gb.add_route(a, lambda p: p, b)  # `a` is now an input pattern
        with pytest.raises(ValueError):
            gb.add_route(c, lambda p: p, a)  # output `a` collides with existing input

    def test_shared_output_pattern_works(self, tmp_path):
        # (3) If output pattern is already an output pattern for another route should work
        root, results = make_dirs(tmp_path)
        gb = GraphBuilder(root, results)
        shared_out = re.compile(r"out\.txt")
        gb.add_route(re.compile(r"a\.txt"), lambda p: p, shared_out)
        gb.add_route(re.compile(r"b\.txt"), lambda p: p, shared_out)
        assert len(gb.router) == 2

    def test_routes_stored_after_chain(self, tmp_path):
        # (4) Check that all routes are stored in .routes after a chain of additions
        root, results = make_dirs(tmp_path)
        gb = GraphBuilder(root, results)
        a = re.compile(r"a\.txt")
        b = re.compile(r"b\.txt")
        c = re.compile(r"c\.txt")
        gb.add_route(a, lambda p: p, re.compile(r"oa\.txt"))
        gb.add_route(b, lambda p: p, re.compile(r"ob\.txt"))
        gb.add_route(c, lambda p: p, re.compile(r"oc\.txt"))
        assert set(gb.router.keys()) == {a, b, c}


class TestBuild:
    def test_ambiguous_path_raises(self, tmp_path):
        # (1) If path matches multiple routes, should raise ValueError
        root, results = make_dirs(tmp_path)
        (root / "file.txt").write_text("x")
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r".*\.txt"), lambda p: p, re.compile(r"o1"))
        gb.add_route(re.compile(r"file\..*"), lambda p: p, re.compile(r"o2"))
        with pytest.raises(ValueError):
            gb.build()

    def test_node_returns_non_path_raises(self, tmp_path):
        # (2) If a node returns a non-Path, should raise TypeError
        root, results = make_dirs(tmp_path)
        (root / "a.dat").write_text("x")
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"a\.dat"), lambda _p: "not a path", re.compile(r"b\.dat"))
        with pytest.raises(TypeError):
            gb.build()

    def test_collision_with_existing_path_raises(self, tmp_path):
        # (3) If a node returns a path that collides with an existing path, should raise ValueError
        root, results = make_dirs(tmp_path)
        (root / "a.dat").write_text("x")
        (root / "keep.txt").write_text("y")  # unmatched, stays put
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"a\.dat"), lambda p: p.parent / "keep.txt", re.compile(r"keep\.txt"))
        with pytest.raises(ValueError):
            gb.build()

    def test_collision_with_previous_node_output_raises(self, tmp_path):
        # (4) If a node returns a path that collides with a path returned by a previously called node,
        #     should raise ValueError
        root, results = make_dirs(tmp_path)
        (root / "f1.dat").write_text("x")
        (root / "f2.dat").write_text("y")
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"f\d\.dat"), lambda p: p.parent / "merged.txt", re.compile(r"merged\.txt"))
        with pytest.raises(ValueError):
            gb.build()

    def test_collision_with_original_path_raises(self, tmp_path):
        # (5) If a node returns a path that collides with an original path, should raise ValueError
        root, results = make_dirs(tmp_path)
        (root / "a.dat").write_text("x")
        (root / "b.dat").write_text("y")

        def swap(p: Path) -> Path:
            return p.parent / ("b.dat" if p.name == "a.dat" else "a.dat")

        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"[ab]\.dat"), swap, re.compile(r"[ba]\.dat"))
        with pytest.raises(ValueError):
            gb.build()

    def test_node_changes_more_than_basename_raises(self, tmp_path):
        # (6) If a node changes something other than the basename, should raise ValueError
        root, results = make_dirs(tmp_path)
        (root / "a.dat").write_text("x")
        other = tmp_path / "other"
        other.mkdir()
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"a\.dat"), lambda _p: other / "a.dat", re.compile(r"out\.dat"))
        with pytest.raises(ValueError):
            gb.build()


class TestSave:
    def test_save_without_build_raises(self, tmp_path):
        # (1) If graph is not built, should raise ValueError
        root, results = make_dirs(tmp_path)
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"a\.dat"), lambda p: p, re.compile(r"b\.dat"))
        with pytest.raises(ValueError):
            gb.save()

    def test_save_with_empty_routes_raises(self, tmp_path):
        # (2) If routes are empty, should raise ValueError
        root, results = make_dirs(tmp_path)
        gb = GraphBuilder(root, results)
        with pytest.raises(ValueError):
            gb.save()

    def test_save_with_conflicting_results_raises(self, tmp_path):
        # (3) If results_path already has a file that would be created by the graph or doesn't exist,
        #     should raise FileExistsError or FileNotFoundError respectively
        root, results = make_dirs(tmp_path)
        (root / "a.dat").write_text("x")
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"a\.dat"), lambda p: p.parent / "b.dat", re.compile(r"b\.dat"))
        gb.build()
        (results / "routes.csv").write_text("")  # a file the graph would create now exists
        with pytest.raises(FileExistsError):
            gb.save()

    def test_save_with_missing_root_raises(self, tmp_path):
        # (4) If root_path doesn't exist, should raise FileNotFoundError
        root, results = make_dirs(tmp_path)
        (root / "a.dat").write_text("x")
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"a\.dat"), lambda p: p.parent / "b.dat", re.compile(r"b\.dat"))
        gb.build()
        shutil.rmtree(root)
        with pytest.raises(FileNotFoundError):
            gb.save()

    def test_saved_rows_match_traversed_paths(self, tmp_path):
        # (5) The sum of the three files rows should be the same as the number of paths beneath the root
        #     (including the root) that do not match the _SKIPS
        root, results = make_dirs(tmp_path)
        (root / "a.dat").write_text("x")
        sub = root / "sub"
        sub.mkdir()
        (sub / "b.dat").write_text("y")
        (sub / "note.md").write_text("z")
        (root / ".DS_Store").write_text("skip")  # skip-listed: never yielded
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"\w+\.dat"), lambda p: p.parent / (p.stem + ".out"), re.compile(r"\w+\.out"))
        gb.build()
        gb.save()
        total_rows = sum(
            len(pd.read_csv(results / name)) for name in ("routable_paths.csv", "problem_paths.csv", "clean_paths.csv")
        )
        expected = sum(1 for _ in bottom_up_traversal(root))
        assert total_rows == expected

    def test_saved_paths_are_bottom_up_ordered(self, tmp_path):
        # (6) Check that the order of paths listed in each file is bottom up ordering.
        root, results = make_dirs(tmp_path)
        (root / "a.dat").write_text("x")
        sub = root / "sub"
        sub.mkdir()
        (sub / "b.dat").write_text("y")
        (sub / "note.md").write_text("z")
        gb = GraphBuilder(root, results)
        gb.add_route(re.compile(r"\w+\.dat"), lambda p: p.parent / (p.stem + ".out"), re.compile(r"\w+\.out"))
        gb.build()
        gb.save()
        # The graph is assembled in bottom-up traversal order; save must preserve
        # that order within each file, so every file's paths form a subsequence.
        rank = {str(entry[0]): i for i, entry in enumerate(gb.graph)}
        for name in ("routable_paths.csv", "problem_paths.csv", "clean_paths.csv"):
            df = pd.read_csv(results / name)
            ranks = [rank[str(p)] for p in df["path"].astype(str)]
            assert ranks == sorted(ranks)
