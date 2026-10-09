from app.services.anomaly import Z_THRESHOLD, robust_z


def test_robust_z_flags_clear_outlier_only():
    history = [0.72, 0.75, 0.71, 0.78, 0.74, 0.76, 0.73, 0.77]
    assert abs(robust_z(0.42, history)) >= Z_THRESHOLD
    assert abs(robust_z(0.74, history)) < 1


def test_robust_z_resists_outliers_in_history():
    history = [10, 11, 9, 10, 100, 10, 11, 9]  # one bad week shouldn't widen the band
    assert abs(robust_z(20, history)) >= Z_THRESHOLD


def test_constant_history():
    assert robust_z(5, [5, 5, 5, 5]) is None
