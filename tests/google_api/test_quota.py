from jev_gmail_labeler.google_api.quota import COSTS, QuotaLimiter


def test_costs_table():
    assert COSTS['messages.get'] == 20
    assert COSTS['history.list'] == 2
    assert COSTS['watch'] == 100


def test_under_budget_does_not_sleep(fake_clock):
    q = QuotaLimiter(100, clock=fake_clock.monotonic, sleep=fake_clock.sleep)
    q.consume(40)
    q.consume(60)
    assert fake_clock.slept == []


def test_over_budget_sleeps_remaining_window(fake_clock):
    q = QuotaLimiter(100, clock=fake_clock.monotonic, sleep=fake_clock.sleep)
    q.consume(60)  # t=0
    fake_clock.advance(10)
    q.consume(40)  # t=10
    fake_clock.advance(5)  # t=15
    q.consume(30)  # must wait until the t=0 entry expires at t=60
    assert fake_clock.slept == [45]
    assert fake_clock.monotonic() == 1060


def test_window_expiry_frees_budget(fake_clock):
    q = QuotaLimiter(100, clock=fake_clock.monotonic, sleep=fake_clock.sleep)
    q.consume(100)
    fake_clock.advance(61)
    q.consume(100)
    assert fake_clock.slept == []


def test_may_need_several_sleeps(fake_clock):
    q = QuotaLimiter(100, clock=fake_clock.monotonic, sleep=fake_clock.sleep)
    q.consume(50)  # t=0
    fake_clock.advance(10)
    q.consume(50)  # t=10
    q.consume(100)  # waits for both entries: 50 then 10 more
    assert fake_clock.slept == [50, 10]


def test_single_call_larger_than_budget_does_not_hang(fake_clock):
    q = QuotaLimiter(100, clock=fake_clock.monotonic, sleep=fake_clock.sleep)
    q.consume(500)
    assert fake_clock.slept == []


def test_default_clock_and_sleep_are_real():
    q = QuotaLimiter()
    q.consume(1)
