import tempfile
from pathlib import Path
from typing import Any, Callable, List
import unittest
from unittest.mock import patch
import builtins

from src.wiktionary_fetcher import WiktionaryFetcher


def mock_open(file, *args, **kwargs):
    # Make importing artifically fail for a certain file
    if file.name == "FAIL.json":
        raise Exception("FAIL")
    return builtins.open(file, *args, **kwargs)


class TestWiktionaryFetcher(unittest.TestCase):
    DICT_NAME = "dict"

    def test_importing(self):
        patcher = patch("src.wiktionary_fetcher.open", side_effect=mock_open)
        patcher.start()
        failed_words = []

        def on_error(word, exc):
            assert str(exc) == "FAIL"
            failed_words.append(word)

        tests_dir = Path(__file__).parent
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_dir = Path(tmp_dir)
            count = WiktionaryFetcher.dump_kaikki_dict(
                tests_dir / "test_dict.json",
                self.DICT_NAME,
                on_progress=lambda _: True,
                on_error=on_error,
                base_dir=tmp_dir,
            )
            assert count == 2
            assert len(failed_words) == 1
            assert failed_words[0] == "FAIL"
            patcher.stop()
            fetcher = WiktionaryFetcher(self.DICT_NAME, base_dir=tmp_dir)
            assert fetcher.get_gender("кошка") == "feminine"
            assert fetcher.get_senses("кошка")[0] == "cat"
            assert fetcher.get_part_of_speech("кошка") == "noun"
            assert (
                fetcher.get_examples("кошка")[0]
                == "жить как ко́шка с соба́кой / to lead a cat-and-dog life"
            )
            methods: List[Callable[[str], Any]] = [
                fetcher.get_examples,
                fetcher.get_gender,
                fetcher.get_part_of_speech,
                fetcher.get_senses,
            ]
            for method in methods:
                try:
                    method("FAIL")
                    assert False
                except:
                    assert True
