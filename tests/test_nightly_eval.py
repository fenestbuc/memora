import json
import urllib.request
from unittest.mock import patch, MagicMock

from memora.nightly_brain import evaluate_rag

@patch("memora.nightly_brain.urllib.request.urlopen")
def test_evaluate_rag_success(mock_urlopen):
    # Setup mock response
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"mrr": 0.85, "hit_rate": 0.9, "total": 10}'
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    result = evaluate_rag("http://localhost", "fake_token")
    
    assert result["status"] == "ok"
    assert result["mrr"] == 0.85
    assert result["hit_rate"] == 0.9

@patch("memora.nightly_brain.urllib.request.urlopen")
def test_evaluate_rag_failure(mock_urlopen):
    mock_urlopen.side_effect = Exception("Connection refused")
    
    result = evaluate_rag("http://localhost", "fake_token")
    assert result["status"] == "failed"
    assert "Connection refused" in result["error"]
