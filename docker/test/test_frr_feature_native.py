#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile and execute the feature descriptor supplied to FRR northbound.

The production declaration and module initializer are compiled unchanged up to
its node callback table. Docker's native FRR integration UUT separately tests
loading the full schema and configuring the named-mode hierarchy.
"""

from pathlib import Path
import os
import re
import subprocess
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "frr/code/eigrp_northbound.c"
SCENARIO = ROOT / "docker/test/uut/frr-feature-native.yaml"


def module_feature_source():
    source = SOURCE.read_text()
    # Include the real feature-list declarations rather than duplicating the
    # contents in the test program. An absent declaration must remain absent.
    declarations = re.findall(
        r"^static const char \*\w+\[\] = \{.*?^\};",
        source, flags=re.MULTILINE | re.DOTALL,
    )
    start = source.index("const struct frr_yang_module_info frr_eigrpd_info = {")
    end = source.index("\t.nodes = {", start)
    initializer = source[start:end] + "};\n"
    return "\n".join(declarations) + "\n" + initializer


def test_frr_eigrp_schema_feature_registration():
    scenario = yaml.safe_load(SCENARIO.read_text())
    assert scenario["schema"] == "docker-frr-feature-uut/v1"
    program = r'''
#include <stdbool.h>
#include <stddef.h>
#include <string.h>

struct frr_yang_module_info {
    const char *name;
    const char **features;
};

''' + module_feature_source() + r'''
static const struct frr_yang_module_info no_features = {
    .name = "frr-eigrpd",
};

static bool enabled(const struct frr_yang_module_info *module, const char *feature)
{
    const char **features = module->features;
    if (!features)
        return false;
    for (; *features; features++) {
        if (strcmp(*features, "*") == 0 || strcmp(*features, feature) == 0)
            return true;
    }
    return false;
}

int main(int argc, char **argv)
{
    const struct frr_yang_module_info *module;
    if (argc != 3)
        return 2;
    module = strcmp(argv[2], "disable") == 0 ? &no_features : &frr_eigrpd_info;
    return enabled(module, argv[1]) ? 0 : 1;
}
'''
    with tempfile.TemporaryDirectory(prefix="oe-frr-feature-") as temp:
        source = Path(temp) / "feature.c"
        binary = Path(temp) / "feature"
        source.write_text(program)
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
                        "-Werror", str(source), "-o", str(binary)], check=True)
        for case in scenario["cases"]:
            mode = "disable" if case.get("without_features") else "enabled"
            result = subprocess.run([str(binary), case["feature"], mode], check=False)
            assert (result.returncode == 0) == case["available"], case["name"]


if __name__ == "__main__":
    test_frr_eigrp_schema_feature_registration()
    print("PASS: FRR EIGRP northbound feature registration")
