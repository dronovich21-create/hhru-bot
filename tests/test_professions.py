import tempfile
import unittest
from pathlib import Path
from hh_bot.professions import ProfessionStore


class ProfessionTests(unittest.TestCase):
    def test_added_names_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'professions.json'
            store = ProfessionStore(path)
            self.assertEqual(store.load(), ['Инженер ПТО'])
            self.assertEqual(store.add('  Сметчик  '), 'Сметчик')
            self.assertEqual(ProfessionStore(path).load(), ['Инженер ПТО', 'Сметчик'])
            self.assertEqual(store.add('сметчик'), 'Сметчик')
            self.assertEqual(len(store.load()), 2)

    def test_invalid_names_not_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProfessionStore(Path(folder) / 'p.json')
            for name in (' ', 'x' * 201):
                with self.assertRaises(ValueError):
                    store.add(name)

    def test_corrupt_list_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'p.json'
            path.write_text('broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                ProfessionStore(path).add('Сметчик')
            self.assertEqual(path.read_text(encoding='utf-8'), 'broken')
