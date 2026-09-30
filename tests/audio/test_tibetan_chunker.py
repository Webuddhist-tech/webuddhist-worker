from worker_api.audio.services.tibetan_chunker import chunk_tibetan_text


class TestChunkTibetanText:
    def test_empty_text_returns_empty_list(self):
        assert chunk_tibetan_text("") == []
        assert chunk_tibetan_text("   ") == []

    def test_text_without_shey_returns_single_chunk(self):
        text = "བོད་སྐད"
        assert chunk_tibetan_text(text) == [text]

    def test_splits_on_shey_and_keeps_delimiter(self):
        text = "བོད་སྐད། བདེ་ལེགས།"
        assert chunk_tibetan_text(text) == ["བོད་སྐད།", "བདེ་ལེགས།"]

    def test_splits_on_nyi_shey(self):
        text = "དང་པོ༎ གཉིས་པ།"
        assert chunk_tibetan_text(text) == ["དང་པོ༎", "གཉིས་པ།"]

    def test_trailing_text_without_shey_is_kept(self):
        text = "བོད་སྐད། བདེ་ལེགས"
        assert chunk_tibetan_text(text) == ["བོད་སྐད།", "བདེ་ལེགས"]
