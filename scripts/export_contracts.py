import json
import tempfile
from pathlib import Path

from korsik.api import create_app
from korsik.config import Settings
from korsik.ml import AnalysisRequest, AnalysisResult, AnswerRequest, AnswerResult, IndexResult
from korsik.nlp_api import AnalysisEvent
from korsik.runtime import Runtime

root = Path(__file__).resolve().parents[1]
contracts = root / "contracts"
contracts.mkdir(exist_ok=True)
(contracts / "nlp-event-candidate-v1.schema.json").write_text(
    json.dumps(
        {"contract_status": "candidate_not_yet_agreed", "schema": AnalysisEvent.model_json_schema()},
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
models = [AnswerRequest, AnswerResult, AnalysisRequest, AnalysisResult, IndexResult]
(contracts / "ml-v1.schema.json").write_text(
    json.dumps(
        {"contract_version": "1", "schemas": {m.__name__: m.model_json_schema() for m in models}},
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
with tempfile.TemporaryDirectory(prefix="korsik-contract-") as directory:
    runtime = Runtime(
        Settings(
            _env_file=None,
            bootstrap_token="documentation-only-token-0000000000000000",
            environment="test",
            database_url="sqlite://",
            data_dir=Path(directory),
            transport="mock",
            ml_mode="stub",
        )
    )
    (contracts / "openapi.json").write_text(
        json.dumps(create_app(runtime).openapi(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    runtime.close()
