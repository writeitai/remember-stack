# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/writeitai/remember-stack/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                                          |    Stmts |     Miss |   Branch |   BrPart |     Cover |   Missing |
|---------------------------------------------------------------------------------------------- | -------: | -------: | -------: | -------: | --------: | --------: |
| src/rememberstack/\_\_init\_\_.py                                                             |        6 |        2 |        0 |        0 |     66.7% |       8-9 |
| src/rememberstack/adapters/\_\_init\_\_.py                                                    |       18 |        2 |        4 |        1 |     86.4% |     35-39 |
| src/rememberstack/adapters/codex\_writer.py                                                   |       82 |        3 |       20 |        3 |     94.1% |172, 203, 215 |
| src/rememberstack/adapters/markitdown\_converter.py                                           |       24 |        2 |        0 |        0 |     91.7% |     40-41 |
| src/rememberstack/adapters/openrouter.py                                                      |      341 |       30 |      114 |       13 |     90.1% |64-65, 71, 153, 349-352, 412, 474-477, 481, 500-506, 516, 521-522, 526, 574-575, 596-597, 599, 603-\>605, 606-\>615, 619, 644-645, 647, 717 |
| src/rememberstack/adapters/postgres\_p1.py                                                    |      401 |       72 |      128 |       34 |     76.9% |114, 154, 168, 200, 229, 262, 294, 316, 363, 410, 498-499, 547, 555-565, 567-568, 619-\>631, 632-633, 690-700, 702-703, 745, 765, 774, 799, 814, 862, 907, 1021, 1096-1102, 1193, 1198, 1288-1309, 1329, 1349-1355, 1387-1402, 1431, 1439, 1453 |
| src/rememberstack/adapters/selfhost/\_\_init\_\_.py                                           |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/forget.py                                                 |       43 |        1 |       10 |        1 |     96.2% |        19 |
| src/rememberstack/adapters/selfhost/git.py                                                    |       98 |        6 |       32 |        8 |     89.2% |55, 75-\>127, 154, 184-185, 199-\>227, 263, 307, 331-\>329 |
| src/rememberstack/adapters/selfhost/minio.py                                                  |      113 |       14 |       30 |        7 |     82.5% |123, 125, 136, 139-144, 177, 207, 219, 223, 245-247, 254 |
| src/rememberstack/adapters/selfhost/mounts.py                                                 |       94 |        3 |       16 |        2 |     95.5% |150-\>173, 171-172, 239 |
| src/rememberstack/adapters/selfhost/object\_store.py                                          |       60 |        2 |       26 |        2 |     95.3% |  102, 104 |
| src/rememberstack/adapters/selfhost/projection.py                                             |       36 |        3 |       12 |        3 |     87.5% |52, 60, 78 |
| src/rememberstack/adapters/selfhost/queue.py                                                  |       65 |        1 |       10 |        2 |     96.0% |111-\>118, 135 |
| src/rememberstack/adapters/selfhost/telemetry.py                                              |       43 |        7 |        6 |        1 |     79.6% | 58, 63-68 |
| src/rememberstack/adapters/selfhost/watcher.py                                                |       31 |        1 |       10 |        1 |     95.1% |        39 |
| src/rememberstack/adapters/sentry.py                                                          |       74 |        9 |       28 |        6 |     85.3% |119-125, 152-\>160, 154-\>160, 157, 166, 169, 172 |
| src/rememberstack/adapters/testing/\_\_init\_\_.py                                            |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/cost\_meter.py                                             |        4 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/model\_provider.py                                         |       35 |        1 |        4 |        1 |     94.9% |        50 |
| src/rememberstack/adapters/testing/queue.py                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/telemetry.py                                               |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/client.py                                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/\_\_init\_\_.py                                                        |       78 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/assured\_operation\_linter.py                                          |       29 |        4 |       18 |        4 |     83.0% |45, 66, 70, 75 |
| src/rememberstack/core/blockizer.py                                                           |       93 |        5 |       36 |        5 |     92.2% |202, 217, 246, 252, 254 |
| src/rememberstack/core/chunker.py                                                             |       73 |        0 |       20 |        0 |    100.0% |           |
| src/rememberstack/core/consumption\_skill.py                                                  |       74 |        0 |        8 |        0 |    100.0% |           |
| src/rememberstack/core/conversion.py                                                          |       39 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/core/core\_manifest.py                                                      |       40 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/core/embedding\_input\_policy.py                                            |      191 |       13 |       74 |        9 |     89.4% |140-\>149, 159, 215-216, 258, 284-290, 310-\>312, 332, 333-\>335, 341 |
| src/rememberstack/core/extension\_packs.py                                                    |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/forget.py                                                              |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_authored.py                                                 |      173 |       26 |       74 |       16 |     83.0% |48-\>59, 117-118, 142, 161, 168, 173-174, 183, 186, 194-197, 213, 217-218, 224, 234-235, 240, 247-249, 253, 255, 258, 264-\>266 |
| src/rememberstack/core/knowledge\_compile.py                                                  |      106 |       11 |       52 |        7 |     86.1% |39, 44, 129, 131, 170-172, 184-186, 202 |
| src/rememberstack/core/knowledge\_fact\_sheet.py                                              |       71 |        3 |       30 |        3 |     94.1% |37, 102, 150 |
| src/rememberstack/core/knowledge\_hashing.py                                                  |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_planner.py                                                  |       35 |        5 |       16 |        5 |     80.4% |33, 35, 37, 50, 52 |
| src/rememberstack/core/knowledge\_writer.py                                                   |       71 |        1 |       30 |        1 |     98.0% |        24 |
| src/rememberstack/core/open\_query\_prose.py                                                  |       18 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/ranking.py                                                             |       66 |        8 |       20 |        6 |     83.7% |60, 125, 127, 166, 178-179, 186, 197 |
| src/rememberstack/core/section\_snap.py                                                       |       63 |        3 |       26 |        3 |     93.3% |122, 177, 203 |
| src/rememberstack/core/storage\_routing.py                                                    |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/structure\_skeleton.py                                                 |      261 |        7 |       90 |        8 |     95.7% |341, 343, 345, 450, 533-\>532, 601, 603, 666 |
| src/rememberstack/eval/\_\_init\_\_.py                                                        |       17 |        2 |        2 |        1 |     84.2% |  141, 150 |
| src/rememberstack/eval/consumption.py                                                         |       43 |        2 |        8 |        2 |     92.2% |    76, 79 |
| src/rememberstack/eval/contradiction.py                                                       |       45 |        1 |       10 |        1 |     96.4% |       111 |
| src/rememberstack/eval/harness.py                                                             |       41 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/eval/lifecycle.py                                                           |       57 |        2 |        8 |        2 |     93.8% |   71, 178 |
| src/rememberstack/eval/operational\_scale.py                                                  |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/eval/resolution.py                                                          |       47 |        1 |       10 |        1 |     96.5% |       153 |
| src/rememberstack/eval/retrieval\_spikes.py                                                   |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/eval/skeleton.py                                                            |       73 |        7 |       26 |        7 |     85.9% |96, 105, 124, 147, 181, 184, 215 |
| src/rememberstack/llm/\_\_init\_\_.py                                                         |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/\_\_init\_\_.py                                                       |      330 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/adjudication.py                                                       |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/assured\_operations.py                                                |       70 |        1 |        6 |        1 |     97.4% |       128 |
| src/rememberstack/model/auth.py                                                               |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/blocks.py                                                             |       28 |        2 |        4 |        2 |     87.5% |    41, 47 |
| src/rememberstack/model/chunks.py                                                             |       81 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/claims.py                                                             |      109 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/client.py                                                             |       66 |        4 |       16 |        1 |     89.0% |   186-189 |
| src/rememberstack/model/clustering.py                                                         |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/component\_version.py                                                 |       66 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/model/consumption.py                                                        |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/conversion.py                                                         |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/deployment.py                                                         |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/documents.py                                                          |       43 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/envelope.py                                                           |      194 |        2 |        8 |        2 |     98.0% |  103, 591 |
| src/rememberstack/model/evaluation.py                                                         |       27 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/forget.py                                                             |       63 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/git.py                                                                |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/knowledge.py                                                          |      374 |       24 |       38 |       14 |     88.8% |232, 271, 356, 368, 387, 399, 429, 586, 603, 631-642, 654-656, 685, 700, 721, 759, 768 |
| src/rememberstack/model/knowledge\_authored.py                                                |      135 |        4 |        8 |        3 |     95.1% |29, 101, 112, 192 |
| src/rememberstack/model/knowledge\_planner.py                                                 |      213 |       15 |       32 |       11 |     88.6% |47, 106, 139, 145, 160, 189, 194, 196, 238, 275-277, 291, 312, 380 |
| src/rememberstack/model/lifecycle.py                                                          |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/model\_provider.py                                                    |       45 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/mounts.py                                                             |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/object\_store.py                                                      |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/operational\_scale.py                                                 |       25 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/operations.py                                                         |       46 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/processing.py                                                         |       90 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/queue.py                                                              |       47 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/relations.py                                                          |       22 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/resolution.py                                                         |       30 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/retrieval\_spikes.py                                                  |       26 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/sections.py                                                           |      183 |        3 |       10 |        3 |     96.9% |278, 282, 288 |
| src/rememberstack/model/telemetry.py                                                          |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/\_\_init\_\_.py                                                       |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/auth.py                                                               |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/connector.py                                                          |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/cost\_meter.py                                                        |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/forget.py                                                             |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/git.py                                                                |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/model\_provider.py                                                    |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/mounts.py                                                             |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/object\_store.py                                                      |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/p1\_index.py                                                          |       53 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/purge.py                                                              |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/queue.py                                                              |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/telemetry.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/profiles/\_\_init\_\_.py                                                    |       10 |        7 |        4 |        0 |     21.4% |     14-22 |
| src/rememberstack/profiles/selfhost.py                                                        |      433 |      212 |       54 |        6 |     46.6% |124, 145, 176, 186, 194, 202-218, 241, 269-299, 312-322, 357-359, 388-447, 567, 574-576, 591-613, 633-635, 639-662, 666-670, 679-842, 851-853, 858-905, 913, 917, 928-929, 1036 |
| src/rememberstack/profiles/selfhost\_forget.py                                                |       54 |       54 |        2 |        0 |      0.0% |     3-150 |
| src/rememberstack/profiles/selfhost\_operations.py                                            |       44 |        5 |        4 |        1 |     87.5% |48, 62-64, 95 |
| src/rememberstack/spine/\_\_init\_\_.py                                                       |       46 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/admission.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/assured\_operations.py                                                |       79 |        3 |        8 |        1 |     95.4% | 37-38, 85 |
| src/rememberstack/spine/backfill.py                                                           |       28 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/catalog\_contract.py                                                  |      151 |       20 |       68 |       21 |     81.3% |463, 518, 537, 565, 578, 619, 632, 647, 669, 684, 695, 715, 770-\>782, 810, 812, 814, 816, 818, 820, 822, 874 |
| src/rememberstack/spine/chunk\_catalog.py                                                     |       65 |        2 |       12 |        2 |     94.8% |   39, 186 |
| src/rememberstack/spine/claim\_catalog.py                                                     |       80 |        8 |       16 |        4 |     85.4% |98-\>107, 130-140, 153, 161, 212 |
| src/rememberstack/spine/clustering.py                                                         |      178 |        6 |       58 |        7 |     94.5% |131, 187, 256, 322-\>305, 483, 516, 521 |
| src/rememberstack/spine/component\_versions.py                                                |       56 |        3 |       12 |        3 |     91.2% |102, 119, 187 |
| src/rememberstack/spine/consumption.py                                                        |       20 |        1 |        2 |        1 |     90.9% |        32 |
| src/rememberstack/spine/cost\_export.py                                                       |      212 |       15 |       40 |        8 |     90.9% |96, 192, 283, 302-303, 336, 344-345, 359, 377, 387-388, 390, 397, 430 |
| src/rememberstack/spine/deployment\_bootstrap.py                                              |       88 |        0 |       16 |        0 |    100.0% |           |
| src/rememberstack/spine/document\_catalog.py                                                  |      154 |        4 |       22 |        5 |     94.9% |147, 215, 244, 449, 460-\>486 |
| src/rememberstack/spine/entity\_registry.py                                                   |       50 |        6 |        4 |        1 |     83.3% |65-\>86, 126, 130-136 |
| src/rememberstack/spine/extension\_packs.py                                                   |       48 |        2 |       20 |        2 |     94.1% |  110, 145 |
| src/rememberstack/spine/fact\_catalog.py                                                      |      170 |       31 |       16 |        1 |     79.6% |124-171, 322-\>324, 389-398, 404-405, 423-424, 448-467, 473-485, 495-506 |
| src/rememberstack/spine/forget.py                                                             |      198 |       42 |       46 |       15 |     71.7% |49, 63-73, 85-90, 99, 118-119, 180, 198-205, 209-213, 219-226, 250, 254, 264, 366-367, 390, 411, 437-450, 480, 508, 525, 584 |
| src/rememberstack/spine/knowledge.py                                                          |     1280 |      123 |      472 |      102 |     86.5% |154, 169, 240, 250, 278, 284-\>exit, 297, 308, 320, 324, 378, 420, 454, 515-520, 544, 612-621, 636, 655, 683-\>679, 699, 752, 766, 878, 919, 949, 1040, 1074, 1094, 1178, 1229, 1265, 1274, 1329, 1349, 1380, 1433-1436, 1463, 1468, 1470, 1472, 1528, 1530, 1532, 1534, 1536, 1553, 1560, 1564, 1595, 1606, 1608-\>1626, 1665, 1781, 1810, 1825, 1832, 1857-1863, 1969-1972, 2081, 2105, 2128, 2169, 2176, 2178-2179, 2186, 2199, 2224-2237, 2298, 2332, 2350, 2360, 2387, 2389, 2393, 2402, 2427, 2455, 2584, 2595, 2599, 2616, 2619, 2691, 2722, 2739, 2767, 2791, 2797-\>2808, 2808-\>2819, 2844, 2860, 2946-\>2951, 2970-2974, 2979-2983, 3114, 3118-\>3131, 3131-\>3143, 3143-\>3150, 3186-3192, 3217-3223, 3264-\>3274, 3274-\>3287, 3402-3406, 3471-3480, 3590, 3609 |
| src/rememberstack/spine/lifecycle.py                                                          |      164 |        6 |       22 |        2 |     94.6% |427, 455-461 |
| src/rememberstack/spine/migrations/\_\_init\_\_.py                                            |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/\_helpers.py                                               |      151 |        9 |       88 |        5 |     93.3% |163-168, 189-191, 198-\>202, 204-\>206, 211 |
| src/rememberstack/spine/migrations/env.py                                                     |       29 |        5 |        6 |        3 |     77.1% |13-\>16, 24, 29-37, 56 |
| src/rememberstack/spine/migrations/versions/\_\_init\_\_.py                                   |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0001\_extensions\_enums.py                |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0002\_infrastructure\_registries.py       |       18 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0003\_entities\_evaluation\_e0\_e1.py     |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0004\_claims\_facts\_evidence.py          |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0005\_projection\_knowledge\_retrieval.py |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0006\_partitions\_views.py                |       18 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p1\_03\_0018\_claimify\_loss\_ledger.py           |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p1\_04\_0019\_d79\_structure\_generations.py      |       26 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p2\_06\_0007\_invalidated\_outcome.py             |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p3\_01\_0008\_document\_version\_target.py        |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p3\_05\_0009\_reconcile\_stage.py                 |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p3\_07\_0010\_lifecycle\_eval\_suite.py           |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p4\_01\_0011\_survivor\_view\_rewrite.py          |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p5\_07\_0020\_retrieval\_batch\_b\_indexes.py     |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p6\_02\_0012\_knowledge\_compile\_recovery.py     |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p6\_04\_0013\_knowledge\_writer\_ledger.py        |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p6\_05\_0014\_knowledge\_planner\_runtime.py      |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p6\_06\_0015\_authored\_dispatch\_runtime.py      |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p7\_02\_0016\_operational\_eval\_suite.py         |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p7\_05\_0017\_hard\_forget.py                     |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p8\_01\_0021\_d80\_embedding\_input.py            |       16 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p8\_01\_0022\_d80\_packaging\_fields.py           |       12 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_01\_0022\_memory\_v1\_query\_space.py         |       31 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_02\_0023\_query\_space\_roles.py              |       55 |        0 |       12 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_03\_0024\_facts\_as\_of.py                    |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_04\_0025\_coordinate\_binding.py              |       63 |        2 |       26 |        2 |     95.5% |  889, 907 |
| src/rememberstack/spine/migrations/versions/p9\_05\_0026\_graph\_helpers.py                   |       56 |        1 |       14 |        1 |     97.1% |       670 |
| src/rememberstack/spine/migrations/versions/p9\_06\_0027\_saved\_query\_registry.py           |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_07\_0028\_chunk\_extract\_indexes.py          |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_08\_0029\_normalize\_claim\_fanout.py         |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_09\_0030\_fact\_authority\_performance.py     |       47 |        1 |       16 |        1 |     96.8% |       267 |
| src/rememberstack/spine/migrations/versions/p9\_10\_0031\_entity\_obs\_flush\_fanout.py       |       21 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_11\_0032\_surface\_cost\_ledger.py            |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_13\_0034\_postgres\_p1\_search.py             |       20 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/spine/observation\_adjudication.py                                          |      246 |       44 |       72 |       19 |     77.7% |148, 162-163, 165, 239, 346-367, 375-396, 448-474, 538-586, 645-\>435, 681-702, 721, 729-741, 802-821, 1038, 1043, 1057-1058, 1076, 1078, 1085-1089 |
| src/rememberstack/spine/operations.py                                                         |       76 |        1 |        4 |        1 |     97.5% |       151 |
| src/rememberstack/spine/projection.py                                                         |      151 |       15 |       10 |        0 |     88.2% |67-68, 321-328, 334-337, 385-386, 397-398 |
| src/rememberstack/spine/query\_space/\_\_init\_\_.py                                          |       40 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/ast\_serializer.py                                       |       27 |        4 |       10 |        2 |     83.8% |77-78, 82, 102 |
| src/rememberstack/spine/query\_space/canonical.py                                             |       71 |        2 |       32 |        1 |     97.1% |   89, 137 |
| src/rememberstack/spine/query\_space/catalog.py                                               |       31 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/deletion\_matrix.py                                      |       80 |        2 |       18 |        1 |     96.9% |  561, 567 |
| src/rememberstack/spine/query\_space/manifest.py                                              |      193 |       18 |       48 |       13 |     87.1% |159, 566, 577, 623, 759, 765, 773, 843, 923, 940, 947-950, 955, 960, 971, 982, 1122, 1124 |
| src/rememberstack/spine/query\_space/quarantine.py                                            |       20 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/source\_definitions.py                                   |      103 |        4 |       32 |        4 |     94.1% |139, 186, 189, 211 |
| src/rememberstack/spine/rank\_embed\_cache.py                                                 |      105 |       11 |       30 |        9 |     85.2% |44, 46, 48, 50, 89, 101, 125-127, 148-\>152, 175, 181 |
| src/rememberstack/spine/readiness.py                                                          |       79 |        1 |       22 |        1 |     98.0% |        60 |
| src/rememberstack/spine/resolver.py                                                           |      189 |       13 |       52 |       12 |     89.6% |221, 223, 231-\>233, 241-247, 308, 318-319, 323, 410, 424, 661, 666, 673 |
| src/rememberstack/spine/review.py                                                             |      120 |        7 |       30 |        7 |     90.7% |125, 185, 255-\>266, 352-356, 403, 405, 658 |
| src/rememberstack/spine/settings.py                                                           |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/supersession.py                                                       |      114 |        9 |       34 |        8 |     88.5% |104, 192, 238, 254, 279-289, 347, 534, 536 |
| src/rememberstack/spine/surface\_cost.py                                                      |      110 |       22 |       10 |        3 |     77.5% |80-81, 111, 139-141, 144-149, 174-176, 180-193 |
| src/rememberstack/spine/sync.py                                                               |       26 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/work\_ledger.py                                                       |      365 |       32 |      122 |       34 |     86.4% |70, 177, 241, 265, 267, 281, 327, 329, 361, 371, 393, 439, 441, 451, 464, 476, 478, 548, 550, 560, 574-\>589, 651, 655, 729, 740, 758, 803, 865, 966, 1009, 1020, 1174, 1209, 1322-\>1326 |
| src/rememberstack/surfaces/\_\_init\_\_.py                                                    |       14 |        2 |        0 |        0 |     85.7% |   121-122 |
| src/rememberstack/surfaces/cli.py                                                             |      511 |      103 |       98 |       18 |     79.1% |58, 66-67, 77-82, 109-114, 133-138, 146-148, 150-158, 168-170, 185-190, 196-198, 209-214, 232, 234-244, 264-272, 274-285, 287-295, 308-313, 315-323, 325-333, 348-352, 361, 367-372, 390-391, 421-424, 431-432, 440-442, 457-459, 485-486, 544-546, 551-\>557, 576, 580-582, 604-606, 614-616, 625-626, 629-631, 703-713 |
| src/rememberstack/surfaces/consumption\_skill.py                                              |       42 |        3 |        8 |        2 |     90.0% |35, 67, 86 |
| src/rememberstack/surfaces/cost\_export\_api.py                                               |      112 |       37 |       24 |        1 |     63.2% |121-125, 146-162, 167-192, 201 |
| src/rememberstack/surfaces/credentials.py                                                     |      112 |       13 |       24 |        7 |     82.4% |82, 97-\>99, 106-108, 114, 119-120, 135, 147-150, 158, 159-\>exit |
| src/rememberstack/surfaces/device\_login.py                                                   |      123 |       25 |       30 |        9 |     73.9% |89, 115, 125-126, 132-133, 141-155, 167-168, 201, 205-206, 208, 234-235, 237-\>239 |
| src/rememberstack/surfaces/graph\_queries.py                                                  |      229 |       14 |       64 |       10 |     91.8% |110-111, 252-253, 267, 271, 338, 342, 439-440, 459, 597-\>602, 619-\>621, 730, 737, 743 |
| src/rememberstack/surfaces/http\_api.py                                                       |      241 |       21 |       36 |        4 |     88.1% |207, 264, 434-437, 458-463, 538-539, 548-573, 641, 656-668, 700-701 |
| src/rememberstack/surfaces/mcp.py                                                             |       71 |        2 |       18 |        2 |     95.5% |   69, 172 |
| src/rememberstack/surfaces/mcp\_memory\_tools.py                                              |      380 |       55 |      162 |       33 |     81.9% |286, 288-296, 299, 366, 411, 478, 482, 492, 503, 511, 670, 728, 749, 760, 882-883, 944, 959-962, 979, 993, 1001, 1013-1014, 1024, 1035, 1046, 1136, 1148, 1155-1156, 1162, 1216, 1340, 1362, 1378-1388, 1395-\>1407, 1447, 1462, 1466, 1470, 1484 |
| src/rememberstack/surfaces/operation\_executor.py                                             |       37 |        3 |        8 |        3 |     86.7% |70, 79, 107 |
| src/rememberstack/surfaces/operation\_surface.py                                              |      131 |       18 |       66 |       11 |     82.2% |108-\>110, 165, 177, 179, 186-188, 194-198, 207, 216-217, 234, 240, 243, 245 |
| src/rememberstack/surfaces/query\_engine.py                                                   |      934 |       66 |      288 |       48 |     89.5% |186-187, 264, 506, 822, 888, 963-970, 972, 980, 1017, 1395, 1459, 1532-1534, 1570, 1587-\>1589, 1590, 1628, 1646, 1685, 1699, 1713-\>1727, 1807, 1881-\>1883, 1884, 2155-2176, 2187, 2231-2254, 2265, 2304, 2338, 2386, 2485-2502, 2507-\>2521, 2571-2579, 2638, 2725, 2729, 2759, 2798-2806, 2820, 2822, 2830, 2852, 2854, 2856, 2864, 2891, 2893, 2933, 3266, 3270 |
| src/rememberstack/surfaces/query\_sandbox/\_\_init\_\_.py                                     |       21 |       14 |        6 |        0 |     25.9% |     50-66 |
| src/rememberstack/surfaces/query\_sandbox/audit.py                                            |       95 |        5 |       14 |        4 |     91.7% |155-\>162, 203-204, 224, 249, 253 |
| src/rememberstack/surfaces/query\_sandbox/bridge.py                                           |      257 |       27 |       80 |       12 |     87.8% |202, 210-211, 218, 264, 280, 294, 335, 337, 360-361, 484-485, 560-561, 580, 596, 621, 633-634, 730, 756, 839, 850-853 |
| src/rememberstack/surfaces/query\_sandbox/cypher.py                                           |      157 |        6 |       68 |        3 |     96.0% |256, 347-349, 365-366 |
| src/rememberstack/surfaces/query\_sandbox/cypher\_executor.py                                 |      317 |       34 |      100 |       21 |     85.9% |226, 286, 318-\>324, 341-\>343, 420, 426, 508, 526, 550, 560-561, 639, 676-677, 787, 800, 813-818, 832-833, 857, 859, 861, 867, 869, 873, 875, 877, 882-888 |
| src/rememberstack/surfaces/query\_sandbox/discovery.py                                        |      108 |        3 |       24 |        3 |     95.5% |119, 189, 237 |
| src/rememberstack/surfaces/query\_sandbox/errors.py                                           |       37 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/examples.py                                         |       57 |        1 |        6 |        1 |     96.8% |       422 |
| src/rememberstack/surfaces/query\_sandbox/executor.py                                         |      253 |       19 |       68 |       11 |     90.0% |93-99, 147, 153, 164, 166, 168, 171-173, 325, 345, 364-369, 605-606 |
| src/rememberstack/surfaces/query\_sandbox/grammar.py                                          |      606 |       56 |      246 |       34 |     88.0% |234, 238, 243, 377, 402, 450, 470-471, 513-\>512, 537, 554-555, 558-567, 590, 673, 679, 731, 735-736, 743, 753, 851, 857-866, 880-883, 889-\>886, 933-\>931, 1004-\>1006, 1024, 1048-\>1052, 1068-1069, 1072, 1097-\>1095, 1104, 1128, 1133-\>1139, 1161, 1167-1173, 1179-1180, 1184-1187, 1219, 1236, 1289 |
| src/rememberstack/surfaces/query\_sandbox/limits.py                                           |       23 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/mcp\_tools.py                                       |      167 |       57 |      106 |       16 |     60.8% |265, 268, 281, 303, 306-\>371, 325, 349, 350-\>371, 362-368, 386, 394, 401, 410, 427-501, 506-511, 516-525, 533-537, 545-551, 611, 622-628 |
| src/rememberstack/surfaces/query\_sandbox/nomination.py                                       |      257 |       28 |       90 |       14 |     87.3% |200-201, 206, 275-283, 290, 479-480, 544, 546, 548, 662-668, 674, 691-692, 729, 751-752, 760, 792-793, 816-\>818 |
| src/rememberstack/surfaces/query\_sandbox/open\_query.py                                      |       75 |        9 |       14 |        3 |     84.3% |74, 81, 140, 158, 189, 246-251, 256 |
| src/rememberstack/surfaces/query\_sandbox/result.py                                           |       65 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/saved\_queries.py                                   |      583 |       66 |      238 |       58 |     84.2% |205, 223, 286, 447, 497, 503, 508, 545, 590, 601, 609, 649, 737, 803, 809, 825, 875-\>885, 943, 964, 1048, 1078, 1101, 1139-\>1141, 1159-1160, 1273, 1359, 1546-\>1548, 1661, 1666, 1750, 1916, 1922, 1942, 1955, 1960, 1969-1980, 1987, 1997, 2003, 2013, 2020, 2037, 2043-2048, 2054, 2075, 2077, 2080, 2100, 2150, 2155, 2172-2177, 2180, 2184, 2188, 2198, 2233, 2250-\>2254, 2265, 2293-2295 |
| src/rememberstack/surfaces/remote\_mcp.py                                                     |      126 |       14 |       40 |        9 |     86.1% |190, 199, 213-214, 221, 227-228, 244, 254, 263, 269, 272, 277, 285 |
| src/rememberstack/surfaces/sdk.py                                                             |      305 |       57 |      110 |       27 |     74.9% |140, 183, 199, 248, 277, 299, 301, 313-327, 343, 345, 370-426, 438, 458-\>460, 461, 463, 464-\>466, 485-\>487, 501, 519, 531, 550, 603, 605, 639, 700-701, 720-\>722, 727-728, 739, 746, 748, 751, 777, 788 |
| src/rememberstack/workers/\_\_init\_\_.py                                                     |       87 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/base.py                                                             |      129 |        1 |       26 |        1 |     98.7% |       188 |
| src/rememberstack/workers/e0.py                                                               |      423 |       23 |       72 |        9 |     93.1% |343-347, 740, 866-883, 943, 1027-\>1026, 1033, 1036, 1132, 1169-1170, 1211, 1276, 1358, 1393, 1454 |
| src/rememberstack/workers/e0\_summary.py                                                      |      392 |       43 |      118 |       22 |     85.3% |351-352, 354-358, 425, 482, 503, 521, 530, 552, 554-556, 570-571, 576, 587, 609, 628, 636, 651, 662, 666-682, 694-697, 707, 717, 906, 917, 928, 945, 957-967 |
| src/rememberstack/workers/e1.py                                                               |      226 |       31 |       76 |       14 |     81.1% |214, 240, 246-\>241, 266-\>250, 310, 324, 344, 370, 381-412, 443, 445, 480-\>482, 482-\>exit, 496-518, 545-548, 693 |
| src/rememberstack/workers/e2.py                                                               |      329 |       18 |      124 |       14 |     92.5% |259, 296, 360-364, 658, 700, 704, 816-817, 818-\>850, 820-\>850, 824, 829, 843, 845-\>850, 952, 971, 1071-1073, 1142 |
| src/rememberstack/workers/e3.py                                                               |      351 |       90 |      124 |       20 |     70.1% |143-149, 161, 175, 203, 229-243, 253, 284-337, 354, 435-438, 554-\>620, 569-\>576, 633, 642, 668, 689, 714, 743-748, 756, 760, 805-872, 935, 944-968, 970, 979 |
| src/rememberstack/workers/forget.py                                                           |      126 |       17 |       26 |        2 |     84.9% |117-122, 177, 188-194, 272-280 |
| src/rememberstack/workers/knowledge\_authored.py                                              |       77 |        5 |       16 |        3 |     91.4% |55, 109, 117, 128-129 |
| src/rememberstack/workers/knowledge\_driver.py                                                |      295 |       53 |       88 |       14 |     77.3% |166, 247-258, 290, 495-511, 515, 562-\>564, 591-611, 625, 629, 633, 641-647, 654-672, 696, 699-700, 702, 705-706, 708, 734 |
| src/rememberstack/workers/knowledge\_fact\_sheet.py                                           |       41 |        2 |        2 |        1 |     93.0% |    31, 57 |
| src/rememberstack/workers/knowledge\_planner.py                                               |      135 |       13 |       20 |        7 |     87.1% |73, 107, 175, 198, 206, 208, 213, 242-250, 273-274 |
| src/rememberstack/workers/knowledge\_writer.py                                                |      156 |       12 |       22 |       11 |     87.1% |74, 94, 140, 174, 191, 196, 198, 297, 334, 340, 342, 363 |
| src/rememberstack/workers/operations.py                                                       |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/p1.py                                                               |       89 |        1 |       10 |        1 |     98.0% |       282 |
| src/rememberstack/workers/p2.py                                                               |      250 |       21 |       62 |       17 |     87.8% |268-277, 333-335, 354-\>371, 394-397, 398-\>402, 421, 515, 520, 538, 551, 556, 569-\>589, 576, 583, 603, 625, 643, 656, 663, 698-\>702 |
| src/rememberstack/workers/p2\_analytics.py                                                    |      107 |        5 |       16 |        1 |     95.1% |213, 233-236 |
| src/rememberstack/workers/p3.py                                                               |      247 |        5 |       68 |        5 |     96.8% |122-127, 226-\>228, 333-\>335, 337-\>341, 564, 671 |
| src/rememberstack/workers/reconcile.py                                                        |      150 |        8 |       38 |       11 |     89.9% |119, 194, 241, 246, 280-\>272, 282, 316, 317-\>322, 326, 399-\>410, 514 |
| src/rememberstack/workers/section\_orientation.py                                             |       48 |        4 |       18 |        4 |     87.9% |46, 83, 95, 97 |
| src/rememberstack/workers/sync.py                                                             |       70 |        0 |       18 |        1 |     98.9% |  108-\>85 |
| **TOTAL**                                                                                     | **22945** | **2178** | **5750** | **1034** | **87.5%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/writeitai/remember-stack/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/writeitai/remember-stack/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/writeitai/remember-stack/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/writeitai/remember-stack/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fwriteitai%2Fremember-stack%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/writeitai/remember-stack/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.