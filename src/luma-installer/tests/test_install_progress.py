"""One install is one job on screen, however many operations it really is."""
import unittest

from luma_installer.progress import Aggregate, Cancelled, Progress, Transaction


def drive(aggregate, operations, steps=5, size=10_000_000):
    """Replay a backend that runs `operations` in turn, each counting 0→100."""
    emitted = []
    for index in range(operations):
        key = f"runtime/dependency.{index}"
        for step in range(steps + 1):
            within = step / steps
            emitted.append(aggregate.report(key, within, int(size * within), size))
    return emitted


class AggregatedProgress(unittest.TestCase):
    def test_ten_dependencies_make_one_forward_sequence(self):
        aggregate = Aggregate()
        aggregate.expect(10, 100_000_000)
        emitted = drive(aggregate, 10)
        self.assertEqual(emitted, sorted(emitted), "progress ran backwards")
        self.assertLessEqual(max(emitted), 1.0)
        self.assertAlmostEqual(emitted[-1], 1.0)
        # The first dependency finishing is a tenth of the job, not all of it.
        self.assertAlmostEqual(emitted[5], .1)

    def test_a_new_operation_never_restarts_the_indicator(self):
        aggregate = Aggregate()
        aggregate.expect(3)
        aggregate.report("a", 1.0)
        before = aggregate.report("a", 1.0)
        self.assertGreaterEqual(aggregate.report("b", 0.0), before)

    def test_an_operation_that_rewinds_itself_does_not_rewind_the_job(self):
        aggregate = Aggregate()
        aggregate.expect(2)
        high = aggregate.report("a", .8)
        self.assertEqual(aggregate.report("a", .1), high)

    def test_more_operations_than_announced_do_not_overflow(self):
        aggregate = Aggregate()
        aggregate.expect(2)
        emitted = drive(aggregate, 5)
        self.assertEqual(emitted, sorted(emitted))
        self.assertLessEqual(max(emitted), 1.0)

    def test_byte_totals_are_the_whole_job_and_never_shrink(self):
        aggregate = Aggregate()
        aggregate.expect(3, 300)
        aggregate.report("a", 1.0, 100, 100)
        aggregate.report("b", .5, 50, 100)
        self.assertEqual(aggregate.transferred_bytes, 150)
        self.assertEqual(aggregate.total_bytes, 300)
        aggregate.report("b", .4, 10, 100)
        self.assertEqual(aggregate.transferred_bytes, 150)
        self.assertEqual(aggregate.total_bytes, 300)

    def test_a_span_keeps_the_job_between_its_stage_markers(self):
        aggregate = Aggregate(1 / 3, .95)
        aggregate.expect(4)
        emitted = drive(aggregate, 4)
        self.assertGreaterEqual(min(emitted), 1 / 3)
        self.assertLessEqual(max(emitted), .95)
        self.assertEqual(emitted, sorted(emitted))


class TransactionProgress(unittest.TestCase):
    def test_one_transaction_is_one_named_sequence(self):
        observed = []
        transaction = Transaction(observed.append, title="Installing Audacity")
        transaction.phase("Check the file", 0, True)
        transaction.phase("Install application", 1 / 3)
        aggregate = Aggregate(1 / 3, .95)
        aggregate.expect(10)
        for value in drive(aggregate, 10):
            transaction.report("org.freedesktop.Platform.GL.default", value)
        transaction.phase("Register", 2 / 3)
        fractions = [event.fraction for event in observed]
        self.assertEqual(fractions, sorted(fractions), "progress ran backwards")
        self.assertEqual({event.label for event in observed}, {"Installing Audacity"})

    def test_a_backend_stage_marker_never_pulls_the_bar_back(self):
        observed = []
        transaction = Transaction(observed.append)
        transaction.report("Installing", .8)
        transaction.phase("Register", 2 / 3)
        self.assertEqual([round(event.fraction, 3) for event in observed], [.8, .8])

    def test_measured_work_is_distinguished_from_a_stage_marker(self):
        observed = []
        transaction = Transaction(observed.append)
        transaction.phase("Prepare application", 1 / 3)
        transaction.report("Installing", .5)
        self.assertEqual([event.measured for event in observed], [False, True])

    def test_reporting_from_a_backend_callback_never_raises(self):
        observed = []
        transaction = Transaction(observed.append)
        transaction.phase("Check the file", 0, True)
        self.assertTrue(transaction.cancel())
        transaction.report("Installing", .5)
        self.assertEqual(len(observed), 2)
        with self.assertRaises(Cancelled):
            transaction.phase("Install application", .6)

    def test_progress_event_stays_backward_compatible(self):
        self.assertEqual(Progress("Installing", .5, False).measured, False)


if __name__ == "__main__":
    unittest.main()
