package com.example;

public class Calculator {

    public int add(int a, int b) {
        return a + b;
    }

    // Deliberately duplicates computeTotal()'s own real body text below,
    // verbatim, as a comment - constructed specifically to make a plain text
    // search for that body ambiguous (matches twice: here, and the real
    // method), while a symbol-name lookup for "computeTotal" is completely
    // unaffected (comments aren't symbols). Positioned directly above
    // computeTotal() deliberately - jdtls attaches a directly-preceding
    // comment to the FOLLOWING symbol's own LSP range (confirmed live via
    // _diagnose.py, not assumed), so this comment must sit next to the
    // method it's actually about, not some unrelated earlier method, or a
    // symbol-based edit of the WRONG method would silently delete it.
    // return a + b + bonus;
    public int computeTotal(int a, int b, int bonus) {
        return a + b + bonus;
    }

    public String describe() {
        return "Calculator";
    }

    public String describe(String prefix) {
        return prefix + ": Calculator";
    }
}
