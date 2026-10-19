from types import SimpleNamespace

from app.workflow.steps.identify_carrier import node
from app.workflow.steps.identify_carrier.models import IdentifyCarrierInput


def _record(carrier: str, biztype: str, index: int) -> dict:
    problem = {
        "service_name": "gather.py.track",
        "biztype": biztype,
        "carrier": carrier,
        "req": "{}",
        "error": "TimeoutError",
    }
    return {
        "biztype": biztype,
        "carrier": carrier,
        "representative_index": index,
        "problem": problem,
    }


def test_identify_carrier_filters_task_rerun_to_exact_carrier_and_biztype(monkeypatch):
    monkeypatch.setattr(node, "records_of", lambda _ctx: [
        _record("CarrierA", "track_server", 0),
        _record("CarrierA", "waybill", 1),
        _record("CarrierB", "track_server", 2),
    ])
    monkeypatch.setattr(node, "is_registered_service", lambda _name: True)

    output = node.run(
        SimpleNamespace(),
        IdentifyCarrierInput(
            run_id="rerun-1",
            task_filter_carrier="CarrierA",
            task_filter_biztype="track_server",
        ),
    )

    assert output.created == 1
    assert [(task.carrier, task.biztype) for task in output.tasks] == [("CarrierA", "track_server")]


def test_identify_carrier_rejects_partial_task_filter(monkeypatch):
    from app.domain.errors import StepFailed

    monkeypatch.setattr(node, "records_of", lambda _ctx: [_record("CarrierA", "track_server", 0)])
    monkeypatch.setattr(node, "is_registered_service", lambda _name: True)

    try:
        node.run(
            SimpleNamespace(),
            IdentifyCarrierInput(run_id="rerun-2", task_filter_carrier="CarrierA"),
        )
    except StepFailed as exc:
        assert "必须同时提供" in str(exc)
    else:
        raise AssertionError("partial task filter must be rejected")
