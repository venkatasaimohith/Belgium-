import io
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

import server
from agents.agent3.frontage_downloader import (
    _cached_metadata,
    address_from_agent1,
    process_site as process_exact_site,
)
from pipeline.runner import PipelineCancelled, member_to_landbase_row, run_pipeline


def test_agent7_floor_count_populates_landbase_floor_column():
    row = member_to_landbase_row(
        {"fid": 1, "Number of Floors": 5},
        row_index=1,
        site_id="SITE_1",
        detect={"vision_count": 1, "flags": []},
        floor={"floor_count": 2, "confidence": "Low", "manual_review": True},
    )
    assert row["Number of Floors"] == 2
    assert row["floor_count_source"] == "agent7_facade"
    assert row["floor_manual_review"] is True
    assert "Floor count 2 requires manual review" in row["Remarks"]


def test_job_flow_and_options_are_persisted():
    job_id = "test-flow-settings"
    server.JOBS_DB[job_id] = {
        "id": job_id,
        "job_id": job_id,
        "status": "UPLOADED",
        "options": {},
    }
    try:
        with patch.object(server, "persist_job"):
            flow_result = server.update_job_flow(
                job_id,
                {
                    "template_id": "custom-1",
                    "flow": [{"key": "agent1_dedup", "enabled": True, "order": 1}],
                },
            )
            option_result = server.update_job_options(
                job_id,
                {
                    "model_mode": "qwen_llm",
                    "views_inspected": 3,
                    "streetview_year": "2021",
                    "use_api_cache": False,
                },
            )

        assert flow_result["flow"][0]["key"] == "agent1_dedup"
        assert server.get_job_flow(job_id)["template_id"] == "custom-1"
        assert option_result["options"]["model_mode"] == "qwen_llm"
        assert option_result["options"]["streetview_year"] == "2021"
        assert option_result["options"]["use_api_cache"] is False
        assert server.JOBS_DB[job_id]["model_mode"] == "qwen_llm"
    finally:
        server.JOBS_DB.pop(job_id, None)


def test_streetview_download_filters_selected_year():
    job_id = "test-streetview-download"
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        output = work / "output"
        images = output / "images"
        images.mkdir(parents=True)
        image_2021 = images / "site_2021.jpg"
        image_2022 = images / "site_2022.jpg"
        image_2021.write_bytes(b"year-2021")
        image_2022.write_bytes(b"year-2022")
        (output / "agent3_images.json").write_text(
            json.dumps([
                {
                    "site_id": "SITE_A",
                    "images": [
                        {"date": "2021-06", "file_path": str(image_2021)},
                        {"date": "2022-06", "file_path": str(image_2022)},
                    ],
                }
            ]),
            encoding="utf-8",
        )
        server.JOBS_DB[job_id] = {
            "job_id": job_id,
            "work_dir": str(work),
            "options": {"streetview_year": "2021"},
        }
        try:
            response = server.download_streetview_images(job_id, year="2021")
            with zipfile.ZipFile(io.BytesIO(response.body)) as bundle:
                names = bundle.namelist()
                assert len(names) == 1
                assert names[0].startswith("2021/")
                assert bundle.read(names[0]) == b"year-2021"
        finally:
            server.JOBS_DB.pop(job_id, None)


def test_stop_job_requests_cooperative_cancellation():
    job_id = "test-stop-job"
    with tempfile.TemporaryDirectory() as td:
        server.JOBS_DB[job_id] = {
            "id": job_id,
            "job_id": job_id,
            "status": "PROCESSING",
            "work_dir": td,
        }
        try:
            with patch.object(server, "persist_job") as persist:
                result = server.stop_job(job_id)

            assert result["status"] == "STOPPING"
            assert server.JOBS_DB[job_id]["cancel_requested"] is True
            assert server.JOBS_DB[job_id]["status"] == "STOPPING"
            assert (Path(td) / ".cancel_requested").is_file()
            persist.assert_called_once_with(server.JOBS_DB[job_id])
        finally:
            server.JOBS_DB.pop(job_id, None)


def test_pipeline_honours_cancellation_before_agent_work():
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        job = {
            "job_id": "test-cancelled-pipeline",
            "work_dir": str(work),
            "input_path": str(work / "unused.gpkg"),
            "options": {},
            "cancel_requested": True,
        }

        result = run_pipeline(job)

        assert result["status"] == "FAILED"
        assert result["error"] == "Stopped by user"
        assert result["cancel_requested"] is False


def test_best_pano_fov90_110_images_are_reused_from_job_output_cache():
    with tempfile.TemporaryDirectory() as td:
        site_dir = Path(td) / "SITE_1"
        site_dir.mkdir()
        image90 = site_dir / "teststraat_10_fov90.jpg"
        image110 = site_dir / "teststraat_10_fov110.jpg"
        image90.write_bytes(b"cached-image-90")
        image110.write_bytes(b"cached-image-110")
        (site_dir / "teststraat_10_meta.json").write_text(
            json.dumps({
                "image_params": {
                    "size": "640x640",
                    "fovs": [90.0, 110.0],
                    "selection_method": "facade_apparent_width_times_frontality",
                    "panorama_selection_version": 7,
                },
                "images": [
                    {"path": str(image90), "fov": 90.0},
                    {"path": str(image110), "fov": 110.0},
                ],
            }),
            encoding="utf-8",
        )

        cached = _cached_metadata(site_dir)

        assert cached is not None
        assert [item["path"] for item in cached["images"]] == [
            str(image90.resolve()), str(image110.resolve())
        ]


def test_agent1_original_fields_build_full_download_address():
    site = {
        "site_id": "SITE_NEW",
        "postcode": "1700",
        "street_norm": "beukendreef",
        "hnr_base": "1",
    }
    members = [{
        "aansluitadres_straatnaam": "Beukendreef",
        "aansluitadres_huisnummer": "1",
        "aansluitadres_postcode": 1700,
        "aansluitadres_gemeente": "Dilbeek",
    }]

    assert address_from_agent1(site, members) == "Beukendreef 1, 1700 Dilbeek, Belgium"


def test_best_pano_downloader_writes_fov90_and_fov110():
    calls = []

    class FakeDownloader:
        @staticmethod
        def geocode_address(address, api_key):
            return {"lat": 50.85, "lng": 4.30, "lambert_x": 1.0, "lambert_y": 2.0}

        @staticmethod
        def get_building_footprint(*args, **kwargs):
            return {"building_geom": object(), "roads_gdf": None, "parcel_geom": None}

        @staticmethod
        def compute_building_centroid(*args, **kwargs):
            return {
                "facade_midpoint_lambert": (1.0, 2.0),
                "centroid_lambert": (1.0, 3.0),
                "facade_edge": object(),
                "outward_normal": (0.0, -1.0),
            }

        @staticmethod
        def download_streetview_image(**kwargs):
            calls.append(kwargs)
            image_path = Path(kwargs["out_path"])
            image_path.write_bytes(b"image")
            return {"path": str(image_path), "heading": kwargs["heading"], "pitch": 0.0, "fov": kwargs["fov"]}

    site = {
        "site_id": "SITE_1",
        "postcode": "1700",
        "street_norm": "beukendreef",
        "hnr_base": "1",
        "frontage_point_a": [1.0, 2.0],
        "frontage_point_b": [3.0, 4.0],
    }
    selected = {
        "pano_id": "pano-1", "cam_lat": 50.85, "cam_lng": 4.30,
        "cam_lambert_x": 140500.0, "cam_lambert_y": 171300.0,
        "date": "2024-06", "heading": 90.0,
    }
    with tempfile.TemporaryDirectory() as td, patch(
        "agents.agent3.frontage_downloader._implementation", return_value=FakeDownloader
    ), patch(
        "agents.agent3.frontage_downloader.discover_panoramas", return_value=[selected]
    ), patch(
        "agents.agent3.frontage_downloader.rank_panoramas", return_value=[selected]
    ):
        result = process_exact_site(
            site,
            api_key="test-key",
            out_images_dir=Path(td),
            agent1_members=[{
                "aansluitadres_straatnaam": "Beukendreef",
                "aansluitadres_huisnummer": "1",
                "aansluitadres_postcode": 1700,
                "aansluitadres_gemeente": "Dilbeek",
            }],
            use_api_cache=False,
        )

    assert [call["fov"] for call in calls] == [90.0, 110.0]
    assert all(call["size"] == "640x640" for call in calls)
    assert [image["fov"] for image in result["images"]] == [90.0, 110.0]
    assert result["images"][1]["fallback_only"] is True


def test_load_jobs_recovers_orphaned_stopping_state():
    with tempfile.TemporaryDirectory() as td:
        jobs_root = Path(td)
        folder = jobs_root / "job-orphaned"
        folder.mkdir()
        job_file = folder / "job.json"
        job_file.write_text(
            json.dumps({
                "job_id": "job-orphaned",
                "status": "STOPPING",
                "cancel_requested": True,
            }),
            encoding="utf-8",
        )
        (folder / ".cancel_requested").write_text("stop", encoding="utf-8")

        with patch.object(server, "JOBS_ROOT", jobs_root):
            server.JOBS_DB.pop("job-orphaned", None)
            server.load_jobs_from_disk()

        recovered = json.loads(job_file.read_text(encoding="utf-8"))
        assert recovered["status"] == "FAILED"
        assert recovered["cancel_requested"] is False
        assert not (folder / ".cancel_requested").exists()
        server.JOBS_DB.pop("job-orphaned", None)


def test_preview_selects_address_layer_in_multilayer_gpkg():
    with tempfile.TemporaryDirectory() as td:
        gpkg = Path(td) / "addresses.gpkg"
        with closing(sqlite3.connect(gpkg)) as conn:
            conn.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)")
            conn.execute("INSERT INTO gpkg_contents VALUES ('target_parcels', 'features')")
            conn.execute("INSERT INTO gpkg_contents VALUES ('address_points', 'features')")
            conn.execute("CREATE TABLE target_parcels (id TEXT, address_id INTEGER)")
            conn.execute("INSERT INTO target_parcels VALUES ('ADP.123', 1)")
            conn.execute(
                "CREATE TABLE address_points ("
                "id INTEGER, aansluitadres_straatnaam TEXT, "
                "aansluitadres_huisnummer TEXT, aansluitadres_postcode INTEGER, "
                "aansluitadres_gemeente TEXT)"
            )
            conn.execute(
                "INSERT INTO address_points VALUES (9954, 'Brusselstraat', '28', 1702, 'Dilbeek')"
            )
            conn.commit()

        records, count = server._parse_preview_records(gpkg)

    assert count == 1
    assert len(records) == 1
    assert records[0]["fid"] == 9954
    assert records[0]["bm_street_name"] == "Brusselstraat"
    assert records[0]["bm_housenumber"] == "28"
    assert records[0]["m_municipality_name"] == "Dilbeek"


def test_preview_and_records_api_preserve_all_uploaded_addresses():
    with tempfile.TemporaryDirectory() as td:
        gpkg = Path(td) / "addresses.gpkg"
        with closing(sqlite3.connect(gpkg)) as conn:
            conn.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)")
            conn.execute("INSERT INTO gpkg_contents VALUES ('addresses', 'features')")
            conn.execute("CREATE TABLE addresses (fid INTEGER, bm_street_name TEXT, bm_housenumber TEXT)")
            conn.executemany(
                "INSERT INTO addresses VALUES (?, 'Teststraat', ?)",
                [(i, str(i)) for i in range(1, 552)],
            )
            conn.commit()
        records, count = server._parse_preview_records(gpkg)
        assert count == len(records) == 551
        job_id = "test-all-records"
        server.JOBS_DB[job_id] = {
            "job_id": job_id,
            "input_path": str(gpkg),
            "records": records[:50],
            "summary_json": {"total_input_records": count},
        }
        try:
            with patch.object(server, "persist_job") as persist:
                first = server.get_job_records(job_id, page=1, limit=500)
                last = server.get_job_records(job_id, page=2, limit=500)
            persist.assert_called_once()
            assert first["count"] == 551
            assert first["total_pages"] == 2
            assert len(first["records"]) == 500
            assert len(last["records"]) == 51
            assert last["records"][-1]["fid"] == 551
        finally:
            server.JOBS_DB.pop(job_id, None)


def test_pipeline_calls_real_bundle_detector_without_legacy_model_arguments():
    import pandas as pd
    from agents.agent4 import agent4_detect
    from agents.agent5 import agent5_detect

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        output = work / "output"
        output.mkdir()
        for name in ("agent2_site.json", "agent3_images.json"):
            (output / name).write_text("[]", encoding="utf-8")
        (output / "agent4_assign.json").write_text(
            json.dumps([{"site_id": "SITE_1", "status": "OK", "images": [
                {"pano_id": "PANO_1", "enhanced_file_path": "enhanced.jpg"}
            ]}]), encoding="utf-8",
        )
        a1 = MagicMock()
        a1.load_input.return_value = pd.DataFrame([{"fid": 1}])
        a1.deduplicate.return_value = (pd.DataFrame([{"member_count": 1}]), {})
        a4a = MagicMock()
        a4a.DEFAULT_WEIGHTS = work / "unused.pth"
        a4a.run.return_value = []
        agents = {
            "agent1_input_dedup": a1,
            "agent2_parcel": MagicMock(),
            "agent3_image": MagicMock(),
            "agent4_assign": a4a,
            "agent5_detect": agent5_detect,
        }
        job = {
            "job_id": "test-bundle-pipeline",
            "work_dir": str(work),
            "input_path": str(work / "input.gpkg"),
            "error": "previous failure",
            "traceback": "previous traceback",
        }
        # Stop at Agent 6: this test exercises the real detection entry point
        # without calling the external target-house vision service.
        def import_agent(folder, module):
            if module == "agent6_reject":
                raise PipelineCancelled("test complete")
            return agents[module]

        with patch.dict("os.environ", {"MAILBOX_ENHANCEMENT_DEVICE": "cuda"}), patch(
            "pipeline.runner._import_agent", side_effect=import_agent
        ), patch.object(
            agent5_detect.RFDetrMailboxBundleDetector, "detect",
            return_value=[agent5_detect.Box(1, 1, 20, 20, 0.9)],
        ) as detect:
            result = run_pipeline(job)

        stage = next(s for s in result["stages"] if s["name"] == "agent5_detect")
        assert stage["status"] == "COMPLETED"
        assert stage["result"]["mailboxes"] == 1
        assert stage["result"]["checkpoint"].endswith("last_ema.pth")
        assert a4a.run.call_args.kwargs["enhancement_device"] == "cuda"
        detect.assert_called_once_with(Path("enhanced.jpg"), 0.7)
        assert "traceback" not in result
        assert result["error"] == "test complete"
