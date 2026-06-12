#!/usr/bin/env python3
"""Build a resume PDF from a YAML data file and a Jinja2 LaTeX template."""

import argparse
import subprocess
import sys
from pathlib import Path

import jinja2
import yaml


def make_env(template_dir: str) -> jinja2.Environment:
    # Use << >> and <% %> delimiters to avoid clashing with LaTeX braces.
    return jinja2.Environment(
        block_start_string="<%",
        block_end_string="%>",
        variable_start_string="<<",
        variable_end_string=">>",
        comment_start_string="<#",
        comment_end_string="#>",
        loader=jinja2.FileSystemLoader(template_dir),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build(data_path: str, template_name: str, output_name: str, output_dir: str = "output") -> None:
    data_file = Path(data_path)
    if not data_file.exists():
        sys.exit(f"Data file not found: {data_path}")

    with data_file.open() as f:
        data = yaml.safe_load(f)

    template_dir = Path(__file__).parent / "templates"
    env = make_env(str(template_dir))

    try:
        template = env.get_template(template_name)
    except jinja2.TemplateNotFound:
        sys.exit(f"Template not found in templates/: {template_name}")

    rendered = template.render(**data)

    # Resolve a relative output dir against the script location so the build
    # works regardless of the current working directory.
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        output_dir = Path(__file__).parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    tex_path = output_dir / f"{output_name}.tex"
    tex_path.write_text(rendered)

    # Run pdflatex twice so cross-references resolve correctly.
    for _ in range(2):
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                f"-output-directory={output_dir}",
                str(tex_path),
            ],
            capture_output=True,
            text=True,
        )

    if result.returncode != 0:
        print("pdflatex error — last lines of log:")
        print("\n".join(result.stdout.splitlines()[-30:]))
        sys.exit(1)

    # Remove auxiliary files.
    for ext in (".aux", ".log", ".out"):
        aux = output_dir / f"{output_name}{ext}"
        if aux.exists():
            aux.unlink()

    try:
        rel = (output_dir / f"{output_name}.pdf").relative_to(Path(__file__).parent)
    except ValueError:
        rel = output_dir / f"{output_name}.pdf"
    print(f"Done: {rel}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build resume PDF from YAML + LaTeX template.")
    parser.add_argument("--data", default="data/resume.yaml", help="Path to YAML data file")
    parser.add_argument(
        "--template",
        default="resume_template.tex",
        help="Template filename inside templates/",
    )
    parser.add_argument("--output", default="resume", help="Output filename (no extension)")
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to write outputs to (created if missing)",
    )
    args = parser.parse_args()

    build(args.data, args.template, args.output, args.output_dir)


if __name__ == "__main__":
    main()
