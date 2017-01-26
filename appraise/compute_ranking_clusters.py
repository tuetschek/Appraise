#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Project: Appraise evaluation system
 Author: Christian Federmann <cfedermann@gmail.com>

usage: compute_ranking_clusters.py

Computes ranking clusters for all WMT16 language pairss.

"""
import os
import sys


if __name__ == "__main__":
    # Properly set DJANGO_SETTINGS_MODULE environment variable.
    os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'
    PROJECT_HOME = os.path.normpath(os.getcwd() + "/..")
    sys.path.append(PROJECT_HOME)

    # We have just added appraise to the system path list, hence this works.
    from appraise.cs_rest.views import update_ranking
    update_ranking()
