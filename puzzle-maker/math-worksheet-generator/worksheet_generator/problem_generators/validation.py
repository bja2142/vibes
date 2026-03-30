from __future__ import annotations

from .base import GeneratedProblem


def verify_generated_problem(problem: GeneratedProblem) -> bool:
    if problem.verification_kind == "equation":
        return _verify_equation_problem(problem)
    if problem.verification_kind == "geometry":
        return _verify_geometry_problem(problem)

    if len(problem.operands) != 2 or not problem.operator:
        return False

    left, right = problem.operands

    if problem.operator == "+":
        expected = left + right
    elif problem.operator == "-":
        expected = left - right
    elif problem.operator == "*":
        expected = left * right
    elif problem.operator == "/":
        if right == 0 or left % right != 0:
            return False
        expected = left // right
    else:
        return False

    return str(expected) == problem.canonical_answer


def _verify_equation_problem(problem: GeneratedProblem) -> bool:
    try:
        solution = int(problem.canonical_answer)
    except ValueError:
        return False

    template = str(problem.metadata.get("template", ""))
    if template == "x_plus_a_equals_b":
        return solution + int(problem.metadata["a"]) == int(problem.metadata["b"])
    if template == "a_plus_x_equals_b":
        return int(problem.metadata["a"]) + solution == int(problem.metadata["b"])
    if template == "x_minus_a_equals_b":
        return solution - int(problem.metadata["a"]) == int(problem.metadata["b"])
    if template == "a_minus_x_equals_b":
        return int(problem.metadata["a"]) - solution == int(problem.metadata["b"])
    if template == "ax_equals_b":
        return int(problem.metadata["a"]) * solution == int(problem.metadata["b"])
    if template == "x_div_a_equals_b":
        divisor = int(problem.metadata["a"])
        return divisor != 0 and solution % divisor == 0 and solution // divisor == int(problem.metadata["b"])
    if template == "ax_plus_b_equals_c":
        return (int(problem.metadata["a"]) * solution) + int(problem.metadata["b"]) == int(problem.metadata["c"])
    if template == "ax_plus_b_equals_cx_plus_d":
        return (
            (int(problem.metadata["a"]) * solution) + int(problem.metadata["b"])
            == (int(problem.metadata["c"]) * solution) + int(problem.metadata["d"])
        )
    if template == "system_x_plus_y_and_y_value":
        return solution + int(problem.metadata["y_value"]) == int(problem.metadata["sum_total"])
    if template == "quadratic_smaller_root":
        a = int(problem.metadata["a"])
        b = int(problem.metadata["b"])
        c = int(problem.metadata["c"])
        smaller_root = int(problem.metadata["smaller_root"])
        return (a * solution * solution) + (b * solution) + c == 0 and solution == smaller_root
    return False


def _verify_geometry_problem(problem: GeneratedProblem) -> bool:
    try:
        solution = int(problem.canonical_answer)
    except ValueError:
        return False

    template = str(problem.metadata.get("template", ""))
    if template == "rectangle_perimeter_missing_width":
        length = int(problem.metadata["length"])
        perimeter = int(problem.metadata["perimeter"])
        return 2 * (length + solution) == perimeter
    if template == "rectangle_area_missing_width":
        length = int(problem.metadata["length"])
        area = int(problem.metadata["area"])
        return length * solution == area
    if template == "right_triangle_missing_leg":
        known_leg = int(problem.metadata["known_leg"])
        hypotenuse = int(problem.metadata["hypotenuse"])
        return (known_leg * known_leg) + (solution * solution) == hypotenuse * hypotenuse
    if template == "right_triangle_tangent":
        adjacent = int(problem.metadata["adjacent"])
        rise = int(problem.metadata["ratio_numerator"])
        run = int(problem.metadata["ratio_denominator"])
        return solution * run == adjacent * rise
    if template == "right_triangle_sine":
        hypotenuse = int(problem.metadata["hypotenuse"])
        rise = int(problem.metadata["ratio_numerator"])
        hyp_ratio = int(problem.metadata["ratio_denominator"])
        return solution * hyp_ratio == hypotenuse * rise
    return False
