# Contributing to pdf-book-to-text

Thank you for your interest in contributing to **pdf-book-to-text**!

This project is authored and maintained by **AZZANI** ([@7AZZANI](https://github.com/7AZZANI)).

---

## Code of Conduct

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). Please treat all contributors with respect.

---

## How Can I Contribute?

### 1. Reporting Bugs
Before creating an issue, please check the [existing issues](https://github.com/7AZZANI/pdf-book-to-text/issues) to avoid duplicates. When filing a bug report, include:
* Operating System and Python version.
* PyMuPDF version (`pip show pymupdf`).
* The exact command executed.
* The error message or unexpected output.
* If possible, a minimal reproducible PDF sample or page extract.

### 2. Suggesting Features
Enhancement ideas are welcome! Please open an issue with:
* A clear description of the proposed feature.
* Real-world use cases (e.g. specific publishing house styles, programming languages, or RAG frameworks).
* Any relevant technical context.

### 3. Submitting Pull Requests
1. **Fork** the repository and create your branch from `main`:
   ```bash
   git checkout -b feature/my-enhancement
   ```
2. **Install** dependencies and test tools:
   ```bash
   pip install -e .[dev]
   ```
3. **Run tests** and ensure 100% pass:
   ```bash
   pytest -v
   ```
4. **Follow conventions**:
   * Code lives cleanly modularized inside `pdf_extractor/`, re-exported by `pdf_book_to_text.py`.
   * Keep body text sanitization safe (never decode HTML or corrupt Unicode box-drawing characters).
   * Ensure Arabic/RTL BiDi handling remains intact.
   * Add new unit tests to `tests/` for any new functionality or bug fix.
5. **Commit your changes**:
   ```bash
   git commit -m "feat(scope): clear description of change"
   ```
6. **Push to your fork** and submit a Pull Request.

---

## Licensing Notice

By submitting a pull request, you agree that your contributions will be licensed under the terms of the project's [LICENSE](LICENSE). Commercial rights remain exclusively with **AZZANI** (`info@7azzani.com`).
