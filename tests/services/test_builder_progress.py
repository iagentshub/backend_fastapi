from app.services.builder_progress import partial_progress


def test_empty_buffer_reports_analyzing():
    assert partial_progress("") == {"stage": "analyzing", "assistant_message": ""}


def test_opening_brace_alone_reports_analyzing():
    progress = partial_progress('{\n  "')
    assert progress["stage"] == "analyzing"
    assert progress["assistant_message"] == ""


def test_reports_replying_once_the_visible_message_starts():
    progress = partial_progress('{"assistant_message": "Voy a prepar')
    assert progress["stage"] == "replying"
    assert progress["assistant_message"] == "Voy a prepar"


def test_reads_the_closed_visible_message():
    progress = partial_progress(
        '{"assistant_message": "He preparado el borrador.", "status": "ready"'
    )
    assert progress["assistant_message"] == "He preparado el borrador."


def test_decodes_escaped_quotes_and_newlines():
    progress = partial_progress(
        '{"assistant_message": "Linea 1\\nDijo \\"hola\\" y siguio'
    )
    assert progress["assistant_message"] == 'Linea 1\nDijo "hola" y siguio'


def test_drops_a_half_received_escape_sequence():
    progress = partial_progress('{"assistant_message": "Primera linea\\')
    assert progress["assistant_message"] == "Primera linea"


def test_keeps_an_escaped_backslash():
    progress = partial_progress('{"assistant_message": "ruta C:\\\\\\\\tmp')
    assert progress["assistant_message"] == "ruta C:\\\\tmp"


def test_reports_drafting_when_the_draft_key_arrives():
    progress = partial_progress(
        '{"assistant_message": "Listo.", "status": "ready", "draft": {"name": "Soporte"'
    )
    assert progress["stage"] == "drafting"
    assert progress["assistant_message"] == "Listo."


def test_reports_writing_instructions_for_an_agent_draft():
    progress = partial_progress(
        '{"assistant_message": "Listo.", "draft": {"name": "X", "system_prompt": "Eres'
    )
    assert progress["stage"] == "writing_instructions"


def test_reports_writing_instructions_for_a_skill_draft():
    progress = partial_progress(
        '{"assistant_message": "Listo.", "draft": {"name": "X", "content": "# Skill'
    )
    assert progress["stage"] == "writing_instructions"


def test_non_json_preamble_does_not_raise():
    progress = partial_progress("Claro, aqui tienes el resultado:")
    assert progress == {"stage": "analyzing", "assistant_message": ""}


def test_key_without_value_yet_is_not_a_message():
    progress = partial_progress('{"assistant_message"')
    assert progress["stage"] == "replying"
    assert progress["assistant_message"] == ""


def test_progress_is_stable_for_the_same_buffer():
    buffer = '{"assistant_message": "Hola"'
    assert partial_progress(buffer) == partial_progress(buffer)
