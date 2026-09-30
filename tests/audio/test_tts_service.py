import pytest
from unittest.mock import Mock, patch, MagicMock
import struct

from worker_api.audio.services.tts_service import (
    generate_tts_audio,
    _normalize_language,
    _generate_gemini_tts_audio,
    _convert_to_wav,
    _parse_audio_mime_type,
    MONLAM_TTS_LANGUAGE,
)
from worker_api.audio.enums import PlanAudioType


class TestNormalizeLanguage:
    def test_normalize_language_lowercase(self):
        assert _normalize_language("EN") == "en"
        assert _normalize_language("Bo") == "bo"
    
    def test_normalize_language_strip(self):
        assert _normalize_language("  en  ") == "en"
        assert _normalize_language(" bo ") == "bo"
    
    def test_normalize_language_none(self):
        assert _normalize_language(None) == "en"
    
    def test_normalize_language_empty(self):
        assert _normalize_language("") == "en"


class TestGenerateTtsAudio:
    def test_empty_content_raises_error(self):
        with pytest.raises(ValueError, match="Content cannot be empty"):
            generate_tts_audio("", PlanAudioType.RECITATION)
    
    def test_whitespace_content_raises_error(self):
        with pytest.raises(ValueError, match="Content cannot be empty"):
            generate_tts_audio("   ", PlanAudioType.RECITATION)
    
    @patch("worker_api.audio.services.tts_service._generate_gemini_tts_audio")
    def test_other_languages_use_gemini(self, mock_gemini):
        mock_gemini.return_value = b"fake_wav_data"

        result = generate_tts_audio("Bonjour le monde", PlanAudioType.RECITATION, language="fr")

        mock_gemini.assert_called_once_with(
            content="Bonjour le monde",
            audio_type=PlanAudioType.RECITATION,
        )
        assert result == b"fake_wav_data"

    @patch("worker_api.audio.services.tts_service.generate_monlam_tts_audio")
    def test_tibetan_language_uses_monlam(self, mock_monlam):
        mock_monlam.return_value = b"fake_audio_data"
        
        result = generate_tts_audio("བོད་སྐད།", PlanAudioType.RECITATION, language="bo")
        
        mock_monlam.assert_called_once_with("བོད་སྐད།", voice_name=None)
        assert result == b"fake_audio_data"
    
    @patch("worker_api.audio.services.tts_service.generate_monlam_tts_audio")
    def test_tibetan_language_with_voice_name(self, mock_monlam):
        mock_monlam.return_value = b"fake_audio_data"
        
        result = generate_tts_audio(
            "བོད་སྐད།", 
            PlanAudioType.RECITATION, 
            language="bo",
            voice_name="custom_voice"
        )
        
        mock_monlam.assert_called_once_with("བོད་སྐད།", voice_name="custom_voice")
        assert result == b"fake_audio_data"
    
    @patch("worker_api.audio.services.tts_service._generate_gemini_tts_audio")
    def test_english_language_uses_gemini(self, mock_gemini):
        mock_gemini.return_value = b"fake_wav_data"
        
        result = generate_tts_audio("Hello world", PlanAudioType.RECITATION, language="en")
        
        mock_gemini.assert_called_once_with(
            content="Hello world",
            audio_type=PlanAudioType.RECITATION
        )
        assert result == b"fake_wav_data"
    
    def test_monlam_language_constant(self):
        assert MONLAM_TTS_LANGUAGE == "bo"


class TestGenerateGeminiTtsAudio:
    @patch("worker_api.audio.services.tts_service.get")
    def test_missing_api_key_raises_error(self, mock_get):
        mock_get.return_value = None
        
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not configured"):
            _generate_gemini_tts_audio("Hello", PlanAudioType.RECITATION)
    
    @patch("worker_api.audio.services.tts_service._convert_to_wav")
    @patch("worker_api.audio.services.tts_service.get")
    def test_successful_generation(self, mock_get, mock_convert):
        mock_get.return_value = "fake_api_key"
        mock_convert.return_value = b"fake_wav_data"
        
        with patch("google.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            
            mock_part = MagicMock()
            mock_part.inline_data.data = b"raw_audio_data"
            mock_part.inline_data.mime_type = "audio/L16;rate=24000"
            
            mock_candidate = MagicMock()
            mock_candidate.content.parts = [mock_part]
            
            mock_response = MagicMock()
            mock_response.candidates = [mock_candidate]
            
            mock_client.models.generate_content.return_value = mock_response
            
            result = _generate_gemini_tts_audio("Hello world", PlanAudioType.RECITATION)
            
            assert result == b"fake_wav_data"
            mock_convert.assert_called_once_with(b"raw_audio_data", "audio/L16;rate=24000")
    
    @patch("worker_api.audio.services.tts_service.get")
    def test_no_candidates_raises_error(self, mock_get):
        mock_get.return_value = "fake_api_key"
        
        with patch("google.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            
            mock_response = MagicMock()
            mock_response.candidates = []
            
            mock_client.models.generate_content.return_value = mock_response
            
            with pytest.raises(RuntimeError, match="TTS generation returned no audio data"):
                _generate_gemini_tts_audio("Hello", PlanAudioType.RECITATION)
    
    @patch("worker_api.audio.services.tts_service.get")
    def test_no_inline_data_raises_error(self, mock_get):
        mock_get.return_value = "fake_api_key"
        
        with patch("google.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            
            mock_part = MagicMock()
            mock_part.inline_data = None
            
            mock_candidate = MagicMock()
            mock_candidate.content.parts = [mock_part]
            
            mock_response = MagicMock()
            mock_response.candidates = [mock_candidate]
            
            mock_client.models.generate_content.return_value = mock_response
            
            with pytest.raises(RuntimeError, match="TTS generation returned no audio data"):
                _generate_gemini_tts_audio("Hello", PlanAudioType.RECITATION)


class TestParseAudioMimeType:
    def test_default_values(self):
        result = _parse_audio_mime_type("audio/wav")
        assert result["bits_per_sample"] == 16
        assert result["rate"] == 24000
    
    def test_parse_rate(self):
        result = _parse_audio_mime_type("audio/L16;rate=48000")
        assert result["rate"] == 48000
        assert result["bits_per_sample"] == 16
    
    def test_parse_bits_per_sample(self):
        result = _parse_audio_mime_type("audio/L24;rate=24000")
        assert result["bits_per_sample"] == 24
        assert result["rate"] == 24000
    
    def test_parse_both_parameters(self):
        result = _parse_audio_mime_type("audio/L32;rate=16000")
        assert result["bits_per_sample"] == 32
        assert result["rate"] == 16000
    
    def test_invalid_rate_uses_default(self):
        result = _parse_audio_mime_type("audio/L16;rate=invalid")
        assert result["rate"] == 24000
    
    def test_invalid_bits_uses_default(self):
        result = _parse_audio_mime_type("audio/Linvalid;rate=24000")
        assert result["bits_per_sample"] == 16


class TestConvertToWav:
    def test_convert_basic_audio(self):
        audio_data = b"\x00\x01" * 100
        mime_type = "audio/L16;rate=24000"
        
        result = _convert_to_wav(audio_data, mime_type)
        
        assert result[:4] == b"RIFF"
        assert result[8:12] == b"WAVE"
        assert result[12:16] == b"fmt "
        assert result[36:40] == b"data"
        assert len(result) > len(audio_data)
    
    def test_convert_different_sample_rate(self):
        audio_data = b"\x00\x01" * 50
        mime_type = "audio/L16;rate=48000"
        
        result = _convert_to_wav(audio_data, mime_type)
        
        assert result[:4] == b"RIFF"
        sample_rate = struct.unpack("<I", result[24:28])[0]
        assert sample_rate == 48000
    
    def test_convert_different_bits_per_sample(self):
        audio_data = b"\x00\x01\x02" * 50
        mime_type = "audio/L24;rate=24000"
        
        result = _convert_to_wav(audio_data, mime_type)
        
        assert result[:4] == b"RIFF"
        bits_per_sample = struct.unpack("<H", result[34:36])[0]
        assert bits_per_sample == 24
    
    def test_wav_header_structure(self):
        audio_data = b"\x00" * 1000
        mime_type = "audio/L16;rate=24000"
        
        result = _convert_to_wav(audio_data, mime_type)
        
        chunk_size = struct.unpack("<I", result[4:8])[0]
        assert chunk_size == 36 + len(audio_data)
        
        data_size = struct.unpack("<I", result[40:44])[0]
        assert data_size == len(audio_data)
        
        assert result[44:] == audio_data
