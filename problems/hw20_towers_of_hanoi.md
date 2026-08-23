# Homework 20: Towers of Hanoi

**Topic:** Recursion
**Difficulty:** Middle

## Problem

Write a function `hanoi(n, source, target, auxiliary, moves)` that solves
the classic Towers of Hanoi puzzle for `n` disks, moving them from the
`source` peg to the `target` peg (using `auxiliary` as a helper peg). The
function should append each move, as a tuple `(disk_from, disk_to)`, to
the `moves` list.

## Input

- `n`: number of disks (positive integer)
- `source`, `target`, `auxiliary`: labels for the three pegs (e.g. `"A"`, `"C"`, `"B"`)
- `moves`: a list that will be filled with the moves

## Output

The `moves` list, filled in order, where each move is a tuple
`(from_peg, to_peg)`. The total number of moves for `n` disks is
`2^n - 1`.

## Example

```
Input:  n = 2, source = "A", target = "C", auxiliary = "B", moves = []
Output moves: [("A", "B"), ("A", "C"), ("B", "C")]
```
