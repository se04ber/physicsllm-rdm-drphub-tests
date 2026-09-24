"""Write the cases.jsonl for the c5_mcp_tool_surface use case.

Kept in the repository because a golden that nobody can trace is a golden
nobody should trust. Every expected value below is followed to the file in
the physics-llm-rdm context release that fixes it, named in `source_file`,
and a phrase whose answer that release does not fix is marked
reference-free rather than given an invented one.

Run it only to regenerate the dataset after the context release changes:

    python build_cases.py
"""

from __future__ import annotations

import json
import pathlib

SUITE = "TestBenchmark_PaN"
USE_CASE = "c5_mcp_tool_surface"
DATASET = "rdm_mcp_live"

VOCAB = "contexts/c5/panet_technique_vocabulary.json"
CROSSWALK = "contexts/c5/technique_schema_crosswalk.json"
REGISTRY = "contexts/c5/c5_rdm_schema_registry_catalog.json"
MAPPINGS = "contexts/c5/c5_rdm_mapping_registry_index.json"
ALPS = "groups/ALPS/references/schema_alps.json"

XRR_URI = "http://purl.org/pan-science/PaNET/PaNET01149"
IMAGING_URI = "http://purl.org/pan-science/PaNET/PaNET01106"
PANET_PATTERN = r"^http://purl\.org/pan-science/PaNET/PaNET\d{5}$"


def case(
    name: str,
    question: str,
    tool: str,
    arguments: dict,
    *,
    expected: str = "",
    extract: str = "",
    metric: str = "exact_match",
    source_file: str = "",
    why: str = "",
    **extra: object,
) -> dict:
    meta = {
        "tool": tool,
        "arguments": arguments,
        "extract": extract,
        "metric": metric,
        "field": f"{tool}.{extract}" if extract else tool,
        "dataset_id": DATASET,
        "use_case_id": USE_CASE,
        "suite": SUITE,
        "golden_source": why,
    }
    meta.update(extra)
    return {
        "name": name,
        "input": question,
        "expected_output": expected,
        # Empty on purpose. The run fills it from the live server, and a
        # case still empty afterwards was never answered, so it is not
        # scored rather than counted as a failure.
        "actual_output": "",
        "context": [],
        "additional_metadata": meta,
        "source_file": source_file,
    }


CASES = [
    # -- resolve_technique --------------------------------------------------
    case(
        "resolve_technique:xrr_phrase_uri",
        'Which PaNET term does the phrase "x-ray reflectivity" resolve to?',
        "resolve_technique",
        {"phrase": "x-ray reflectivity"},
        expected=XRR_URI,
        extract="uri",
        source_file=VOCAB,
        why="the pinned vocabulary maps the label 'x-ray reflectivity' to this uri",
    ),
    case(
        "resolve_technique:xrr_alias_uri",
        'Which PaNET term does the abbreviation "XRR" resolve to?',
        "resolve_technique",
        {"phrase": "XRR"},
        expected=XRR_URI,
        extract="uri",
        source_file=VOCAB,
        why="'xrr' is an alias of the x-ray reflectivity entry in the pinned vocabulary",
    ),
    case(
        "resolve_technique:gixos_alias_label",
        'Which technique label does the beamline word "gixos" resolve to?',
        "resolve_technique",
        {"phrase": "gixos"},
        expected="x-ray reflectivity",
        extract="label",
        source_file=VOCAB,
        why="'gixos' is an alias of the x-ray reflectivity entry in the pinned vocabulary",
    ),
    case(
        "resolve_technique:tomography_uri",
        'Which PaNET term does the phrase "tomography" resolve to?',
        "resolve_technique",
        {"phrase": "tomography"},
        expected=IMAGING_URI,
        extract="uri",
        source_file=VOCAB,
        why="'tomography' is an alias of the imaging entry in the pinned vocabulary",
    ),
    case(
        "resolve_technique:answer_came_from_the_pinned_list",
        'Did the answer for "x-ray reflectivity" come from the pinned list or from live SPARQL?',
        "resolve_technique",
        {"phrase": "x-ray reflectivity"},
        expected="pinned",
        extract="source",
        source_file=VOCAB,
        why=(
            "a phrase the context release carries is answered from it, so the "
            "resolver reports 'pinned' and never reaches the live endpoint"
        ),
    ),
    case(
        "resolve_technique:uri_shape",
        'Is the term returned for "XRR" shaped like a PaNET URI at all?',
        "resolve_technique",
        {"phrase": "XRR"},
        expected=PANET_PATTERN,
        extract="uri",
        metric="pattern_match",
        pattern=PANET_PATTERN,
        source_file=VOCAB,
        why="every uri in the pinned vocabulary is a PaNET purl with a five-digit id",
    ),
    case(
        "resolve_technique:out_of_vocabulary",
        'What does the server answer for "trombone lubrication rate", which is not a technique?',
        "resolve_technique",
        {"phrase": "trombone lubrication rate"},
        extract="",
        metric="reference_free",
        source_file=VOCAB,
        reference_free_reason=(
            "the pinned vocabulary carries no such term, and the tool then "
            "consults PaNET's live SPARQL endpoint, whose answer this card does "
            "not control. The response is recorded and nothing is gated on it."
        ),
        why="no expected value can be stated, so none is invented",
    ),
    # -- find_schema_for ----------------------------------------------------
    case(
        "find_schema_for:xrr_outcome",
        "How sure is the server that a schema covers x-ray reflectivity?",
        "find_schema_for",
        {"technique": "x-ray reflectivity"},
        expected="exact",
        extract="outcome",
        source_file=CROSSWALK,
        why="the crosswalk carries an entry for this PaNET uri, which is reported as 'exact'",
    ),
    case(
        "find_schema_for:xrr_schema_id",
        "Which schema covers x-ray reflectivity?",
        "find_schema_for",
        {"technique": "x-ray reflectivity"},
        expected="Reflectivity",
        extract="schema_id",
        source_file=CROSSWALK,
        why="the crosswalk entry for PaNET01149 names schema_id 'Reflectivity'",
    ),
    case(
        "find_schema_for:xrr_target_repository",
        "Which repository accepts the schema that covers x-ray reflectivity?",
        "find_schema_for",
        {"technique": "x-ray reflectivity"},
        expected="sisyphos-reflectivity-test-stage",
        extract="target_repository",
        source_file=CROSSWALK,
        why="the crosswalk entry for PaNET01149 names this target repository",
    ),
    case(
        "find_schema_for:xrr_review_status",
        "Has the mapping from this technique to that schema been reviewed?",
        "find_schema_for",
        {"technique": "x-ray reflectivity"},
        expected="needs_review",
        extract="review_status",
        source_file=CROSSWALK,
        why=(
            "the crosswalk entry records status 'needs_review'. A card that "
            "reported it as reviewed would be the failure this case catches"
        ),
    ),
    case(
        "find_schema_for:xrr_panet_uri_shape",
        "Is the technique term in the schema answer a PaNET URI?",
        "find_schema_for",
        {"technique": "x-ray reflectivity"},
        expected=PANET_PATTERN,
        extract="panet_uri",
        metric="pattern_match",
        pattern=PANET_PATTERN,
        source_file=CROSSWALK,
        why="find_schema_for reports the uri it resolved, which is a PaNET purl",
    ),
    case(
        "find_schema_for:xrr_answer_shape",
        "Does the schema answer carry the whole chain it is supposed to show?",
        "find_schema_for",
        {"technique": "x-ray reflectivity"},
        extract="",
        metric="json_correctness",
        json_required=[
            "technique",
            "panet_uri",
            "panet_label",
            "outcome",
            "candidates",
            "start_from",
            "note",
            "next_step",
        ],
        json_optional=["schema_id", "target_repository", "review_status"],
        source_file="services/mcp/src/rdm_mcp/server.py",
        why=(
            "SchemaSearch.as_dict in physics_llm.schema_discovery returns exactly "
            "these keys, and the chain is the point of the tool"
        ),
    ),
    case(
        "find_schema_for:tomography_schema_id",
        "Which schema covers tomography?",
        "find_schema_for",
        {"technique": "tomography"},
        expected="p05_imaging",
        extract="schema_id",
        source_file=CROSSWALK,
        why="the crosswalk entry for PaNET01106 names schema_id 'p05_imaging'",
    ),
    # -- list_schemas -------------------------------------------------------
    case(
        "list_schemas:registry_id",
        "Which registry document is the schema list drawn from?",
        "list_schemas",
        {},
        expected="c5_rdm_schema_registry_catalog",
        extract="registry_id",
        source_file=REGISTRY,
        why="the registry catalog's own id field",
    ),
    case(
        "list_schemas:answer_shape",
        "Does the schema list say where it came from and how to check it?",
        "list_schemas",
        {},
        extract="",
        metric="json_correctness",
        json_required=["registry_id", "registry_version", "checksum", "schemas"],
        source_file="services/mcp/src/rdm_mcp/rdm_registry.py",
        why="rdm_registry.list_schemas returns exactly these four keys",
    ),
    # -- describe_schema ----------------------------------------------------
    case(
        "describe_schema:alps_dialect",
        "Which JSON Schema dialect does the alps schema declare?",
        "describe_schema",
        {"schema_id": "alps"},
        expected="https://json-schema.org/draft/2020-12/schema",
        extract="$schema",
        source_file="services/mcp/src/rdm_mcp/rdm_registry.py",
        why="_alps_json_schema emits draft 2020-12",
    ),
    case(
        "describe_schema:alps_type",
        "What kind of document does the alps schema describe?",
        "describe_schema",
        {"schema_id": "alps"},
        expected="object",
        extract="type",
        source_file="services/mcp/src/rdm_mcp/rdm_registry.py",
        why="_alps_json_schema emits an object schema",
    ),
    case(
        "describe_schema:alps_title",
        "What is the alps schema called?",
        "describe_schema",
        {"schema_id": "alps"},
        expected="EasyLog ALPS Entry",
        extract="title",
        source_file=ALPS,
        why="the title comes from the EasyLog document's own title field",
    ),
    case(
        "describe_schema:alps_answer_shape",
        "Is the alps answer a usable JSON Schema rather than a description of one?",
        "describe_schema",
        {"schema_id": "alps"},
        extract="",
        metric="json_correctness",
        json_required=["title", "type", "properties", "required", "additionalProperties"],
        source_file="services/mcp/src/rdm_mcp/rdm_registry.py",
        why=(
            "_alps_json_schema emits these keys. $schema and $id are checked "
            "separately because a JSON key starting with $ cannot be a field "
            "name in the model this metric builds"
        ),
    ),
    case(
        "describe_schema:refuses_unknown_id",
        "Does the server refuse a schema id it cannot back with a real document?",
        "describe_schema",
        {"schema_id": "definitely-not-a-registered-schema"},
        expected="error",
        extract="",
        observe="call_outcome",
        source_file="services/mcp/src/rdm_mcp/rdm_registry.py",
        why=(
            "describe_schema raises SchemaUnavailableError for an unregistered "
            "id, which reaches the client as an MCP tool error. Returning "
            "anything at all here would be the fabrication the tool exists to "
            "avoid, so the refusal is the correct answer"
        ),
    ),
    # -- mappings -----------------------------------------------------------
    case(
        "describe_mapping:nexus_target_schema",
        "Which schema does the reflectivity NeXus mapping target?",
        "describe_mapping",
        {"mapping_id": "reflectivity_nexus_v1"},
        expected="Reflectivity",
        extract="mapping.target_schema_id",
        source_file=MAPPINGS,
        why="the mapping registry entry names this target schema id",
    ),
    case(
        "describe_mapping:nexus_artifact_ref",
        "Which code implements the reflectivity NeXus mapping?",
        "describe_mapping",
        {"mapping_id": "reflectivity_nexus_v1"},
        expected="C5_testbackend/backendLogic/rdm/mappings.py#REFLECTIVITY_NEXUS_V1",
        extract="mapping.artifact_ref",
        source_file=MAPPINGS,
        why=(
            "the registry records the artifact_ref so the coverage claim can be "
            "checked rather than taken on trust"
        ),
    ),
    case(
        "list_mappings:registry_id",
        "Which registry document is the mapping list drawn from?",
        "list_mappings",
        {"schema_id": "Reflectivity"},
        expected="c5_rdm_mapping_registry_index",
        extract="registry_id",
        source_file=MAPPINGS,
        why="the mapping registry index's own id field",
    ),
]


def main() -> None:
    out = pathlib.Path("benchmark") / SUITE / "datasets" / USE_CASE / DATASET / "cases.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in CASES)
    )
    print(f"{len(CASES)} cases -> {out}")


if __name__ == "__main__":
    main()
