from foley.keywords import ACTIONS, MATERIALS, has_word

def test_has_word_prefix_match_at_boundary():
    assert has_word("wooden door closing", "wood")
    assert has_word("the lighting flickers", "light")

def test_has_word_rejects_mid_word_match():
    assert not has_word("bright sunlight", "light")
    assert not has_word("unstoppable", "stop")

def test_has_word_exact_word():
    assert has_word("footsteps on gravel", "gravel")
    assert not has_word("footsteps on grass", "gravel")

def test_keyword_tables_exported():
    assert "gravel" in MATERIALS
    assert "footsteps" in ACTIONS
    triggers, accepted = ACTIONS["strike"]
    assert "striking" in triggers and "ignite" in accepted
