# Homework 15: Count Inversions

**Topic:** Sorting
**Difficulty:** Middle

## Problem

An **inversion** in an array is a pair of indices `(i, j)` such that
`i < j` and `arr[i] > arr[j]`. Write a function `count_inversions(arr)`
that returns the total number of inversions in the array.

A simple `O(n^2)` solution is acceptable for this homework.

## Input

A list of numbers, e.g. `[2, 4, 1, 3, 5]`.

## Output

An integer: the number of inversions in the list.

## Example

```
Input:  arr = [2, 4, 1, 3, 5]
Output: 3   # inversions: (2,1), (4,1), (4,3)
```
