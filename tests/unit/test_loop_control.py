from freecad_ai.core.loop_control import should_continue_loop


def test_bounded_continues_until_limit():
    assert should_continue_loop(30, 0, False) is True
    assert should_continue_loop(30, 29, False) is True
    assert should_continue_loop(30, 30, False) is False


def test_endless_always_continues():
    assert should_continue_loop(0, 0, False) is True
    assert should_continue_loop(0, 100000, False) is True


def test_interrupt_stops_regardless():
    assert should_continue_loop(30, 0, True) is False
    assert should_continue_loop(0, 0, True) is False
