# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/writeitai/remember-stack/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                                          |    Stmts |     Miss |   Branch |   BrPart |     Cover |   Missing |
|---------------------------------------------------------------------------------------------- | -------: | -------: | -------: | -------: | --------: | --------: |
| src/rememberstack/\_\_init\_\_.py                                                             |        6 |        2 |        0 |        0 |     66.7% |       8-9 |
| src/rememberstack/adapters/\_\_init\_\_.py                                                    |       17 |        3 |        4 |        2 |     76.2% | 33-37, 42 |
| src/rememberstack/adapters/codex\_writer.py                                                   |       82 |        3 |       20 |        3 |     94.1% |172, 203, 215 |
| src/rememberstack/adapters/markitdown\_converter.py                                           |       24 |        2 |        0 |        0 |     91.7% |     40-41 |
| src/rememberstack/adapters/openrouter.py                                                      |      187 |       10 |       56 |        4 |     93.4% |244-250, 295-296, 317-318, 320, 324-\>326, 327-\>336, 340 |
| src/rememberstack/adapters/selfhost/\_\_init\_\_.py                                           |       24 |        1 |        2 |        1 |     92.3% |        54 |
| src/rememberstack/adapters/selfhost/forget.py                                                 |       43 |        1 |       10 |        1 |     96.2% |        19 |
| src/rememberstack/adapters/selfhost/git.py                                                    |       98 |        6 |       32 |        8 |     89.2% |55, 75-\>127, 154, 184-185, 199-\>227, 263, 307, 331-\>329 |
| src/rememberstack/adapters/selfhost/lance.py                                                  |      124 |       12 |       40 |       15 |     83.5% |46, 67, 131, 154, 156, 158-\>160, 180-\>185, 185-\>exit, 196, 278, 284, 286, 291, 299, 316 |
| src/rememberstack/adapters/selfhost/minio.py                                                  |      113 |       14 |       30 |        7 |     82.5% |123, 125, 136, 139-144, 177, 207, 219, 223, 245-247, 254 |
| src/rememberstack/adapters/selfhost/mounts.py                                                 |       94 |        3 |       16 |        2 |     95.5% |150-\>173, 171-172, 239 |
| src/rememberstack/adapters/selfhost/object\_store.py                                          |       60 |        2 |       26 |        2 |     95.3% |  102, 104 |
| src/rememberstack/adapters/selfhost/projection.py                                             |       36 |        3 |       12 |        3 |     87.5% |52, 60, 78 |
| src/rememberstack/adapters/selfhost/queue.py                                                  |       65 |        1 |       10 |        2 |     96.0% |111-\>118, 135 |
| src/rememberstack/adapters/selfhost/telemetry.py                                              |       21 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/watcher.py                                                |       31 |        1 |       10 |        1 |     95.1% |        39 |
| src/rememberstack/adapters/testing/\_\_init\_\_.py                                            |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/cost\_meter.py                                             |        4 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/model\_provider.py                                         |       35 |        1 |        4 |        1 |     94.9% |        50 |
| src/rememberstack/adapters/testing/queue.py                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/telemetry.py                                               |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/client.py                                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/\_\_init\_\_.py                                                        |       62 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/blockizer.py                                                           |       70 |        2 |       24 |        2 |     95.7% |  165, 178 |
| src/rememberstack/core/chunker.py                                                             |       73 |        0 |       20 |        0 |    100.0% |           |
| src/rememberstack/core/consumption\_skill.py                                                  |       55 |        1 |        6 |        1 |     96.7% |       258 |
| src/rememberstack/core/conversion.py                                                          |       36 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/core/core\_manifest.py                                                      |       40 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/core/extension\_packs.py                                                    |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/forget.py                                                              |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_authored.py                                                 |      173 |       26 |       74 |       16 |     83.0% |48-\>59, 117-118, 142, 161, 168, 173-174, 183, 186, 194-197, 213, 217-218, 224, 234-235, 240, 247-249, 253, 255, 258, 264-\>266 |
| src/rememberstack/core/knowledge\_compile.py                                                  |      106 |       11 |       52 |        7 |     86.1% |39, 44, 129, 131, 170-172, 184-186, 202 |
| src/rememberstack/core/knowledge\_fact\_sheet.py                                              |       71 |        3 |       30 |        3 |     94.1% |37, 102, 150 |
| src/rememberstack/core/knowledge\_hashing.py                                                  |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_planner.py                                                  |       35 |        5 |       16 |        5 |     80.4% |33, 35, 37, 50, 52 |
| src/rememberstack/core/knowledge\_writer.py                                                   |       71 |        1 |       30 |        1 |     98.0% |        24 |
| src/rememberstack/core/ranking.py                                                             |       66 |        8 |       20 |        6 |     83.7% |60, 125, 127, 166, 178-179, 186, 197 |
| src/rememberstack/core/recipe\_linter.py                                                      |       48 |        6 |       34 |        6 |     85.4% |113, 119, 130, 145, 151, 157 |
| src/rememberstack/core/section\_snap.py                                                       |       63 |        3 |       26 |        3 |     93.3% |122, 177, 203 |
| src/rememberstack/core/storage\_routing.py                                                    |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/eval/\_\_init\_\_.py                                                        |       25 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/eval/consumption.py                                                         |       43 |        2 |        8 |        2 |     92.2% |    76, 79 |
| src/rememberstack/eval/contradiction.py                                                       |       45 |        1 |       10 |        1 |     96.4% |       111 |
| src/rememberstack/eval/harness.py                                                             |       41 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/eval/lifecycle.py                                                           |       57 |        2 |        8 |        2 |     93.8% |   71, 178 |
| src/rememberstack/eval/operational\_scale.py                                                  |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/eval/resolution.py                                                          |       47 |        1 |       10 |        1 |     96.5% |       153 |
| src/rememberstack/eval/retrieval\_spikes.py                                                   |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/eval/skeleton.py                                                            |       73 |        7 |       26 |        7 |     85.9% |96, 105, 124, 147, 181, 184, 215 |
| src/rememberstack/llm/\_\_init\_\_.py                                                         |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/\_\_init\_\_.py                                                       |      303 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/adjudication.py                                                       |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/auth.py                                                               |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/blocks.py                                                             |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/chunks.py                                                             |       47 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/claims.py                                                             |      104 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/client.py                                                             |       63 |        4 |       16 |        1 |     88.6% |   180-183 |
| src/rememberstack/model/clustering.py                                                         |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/component\_version.py                                                 |       65 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/model/consumption.py                                                        |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/conversion.py                                                         |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/deployment.py                                                         |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/documents.py                                                          |       43 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/envelope.py                                                           |      151 |        0 |       10 |        0 |    100.0% |           |
| src/rememberstack/model/evaluation.py                                                         |       27 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/forget.py                                                             |       63 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/git.py                                                                |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/knowledge.py                                                          |      374 |       24 |       38 |       14 |     88.8% |232, 271, 356, 368, 387, 399, 429, 586, 603, 631-642, 654-656, 685, 700, 721, 759, 768 |
| src/rememberstack/model/knowledge\_authored.py                                                |      135 |        4 |        8 |        3 |     95.1% |29, 101, 112, 192 |
| src/rememberstack/model/knowledge\_planner.py                                                 |      213 |       15 |       32 |       11 |     88.6% |47, 106, 139, 145, 160, 189, 194, 196, 238, 275-277, 291, 312, 380 |
| src/rememberstack/model/lifecycle.py                                                          |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/model\_provider.py                                                    |       40 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/mounts.py                                                             |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/object\_store.py                                                      |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/operational\_scale.py                                                 |       25 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/operations.py                                                         |       46 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/processing.py                                                         |       90 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/queue.py                                                              |       47 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/recipes.py                                                            |       23 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/relations.py                                                          |       22 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/resolution.py                                                         |       30 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/retrieval\_spikes.py                                                  |       26 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/sections.py                                                           |       60 |        3 |       10 |        3 |     91.4% |90, 94, 100 |
| src/rememberstack/model/telemetry.py                                                          |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/\_\_init\_\_.py                                                       |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/auth.py                                                               |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/connector.py                                                          |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/cost\_meter.py                                                        |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/forget.py                                                             |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/git.py                                                                |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/model\_provider.py                                                    |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/mounts.py                                                             |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/object\_store.py                                                      |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/p1\_index.py                                                          |       27 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/purge.py                                                              |       21 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/queue.py                                                              |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/telemetry.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/profiles/\_\_init\_\_.py                                                    |       10 |        7 |        4 |        0 |     21.4% |     14-22 |
| src/rememberstack/profiles/selfhost.py                                                        |      285 |      182 |       38 |        1 |     32.2% |109, 113-119, 137-143, 148-150, 174, 178-205, 212-286, 290-305, 325-327, 331-354, 358-494, 499, 504-541, 546-547, 583, 630 |
| src/rememberstack/profiles/selfhost\_forget.py                                                |       55 |       55 |        2 |        0 |      0.0% |     3-153 |
| src/rememberstack/profiles/selfhost\_operations.py                                            |       44 |        5 |        4 |        1 |     87.5% |48, 62-64, 95 |
| src/rememberstack/spine/\_\_init\_\_.py                                                       |       48 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/admission.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/backfill.py                                                           |       42 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/spine/catalog\_contract.py                                                  |      138 |       19 |       60 |       20 |     80.3% |413, 432, 460, 473, 514, 527, 542, 564, 579, 590, 600, 610, 664-\>676, 698, 700, 702, 704, 706, 708, 757 |
| src/rememberstack/spine/chunk\_catalog.py                                                     |       50 |        2 |       10 |        2 |     93.3% |   39, 143 |
| src/rememberstack/spine/claim\_catalog.py                                                     |       62 |        2 |       14 |        3 |     93.4% |97-\>106, 149, 161 |
| src/rememberstack/spine/clustering.py                                                         |      178 |        6 |       58 |        7 |     94.5% |131, 187, 256, 322-\>305, 483, 516, 521 |
| src/rememberstack/spine/component\_versions.py                                                |       56 |        3 |       12 |        3 |     91.2% |102, 119, 187 |
| src/rememberstack/spine/consumption.py                                                        |       20 |        1 |        2 |        1 |     90.9% |        32 |
| src/rememberstack/spine/deployment\_bootstrap.py                                              |       88 |        0 |       16 |        0 |    100.0% |           |
| src/rememberstack/spine/document\_catalog.py                                                  |      103 |        2 |       14 |        2 |     96.6% |  138, 206 |
| src/rememberstack/spine/entity\_registry.py                                                   |       50 |        2 |        4 |        2 |     92.6% |65-\>86, 126, 131 |
| src/rememberstack/spine/extension\_packs.py                                                   |       48 |        2 |       20 |        2 |     94.1% |  110, 145 |
| src/rememberstack/spine/fact\_catalog.py                                                      |      124 |       10 |       12 |        1 |     90.4% |122-169, 298-\>300 |
| src/rememberstack/spine/forget.py                                                             |      198 |       42 |       46 |       15 |     71.7% |49, 63-73, 85-90, 99, 118-119, 180, 198-205, 209-213, 219-226, 250, 254, 264, 366-367, 390, 411, 437-450, 480, 508, 525, 584 |
| src/rememberstack/spine/knowledge.py                                                          |     1280 |      123 |      472 |      102 |     86.5% |154, 169, 240, 250, 278, 284-\>exit, 297, 308, 320, 324, 378, 420, 454, 515-520, 544, 612-621, 636, 655, 683-\>679, 699, 752, 766, 878, 919, 949, 1040, 1074, 1094, 1178, 1229, 1265, 1274, 1329, 1349, 1380, 1433-1436, 1463, 1468, 1470, 1472, 1528, 1530, 1532, 1534, 1536, 1553, 1560, 1564, 1595, 1606, 1608-\>1626, 1665, 1781, 1810, 1825, 1832, 1857-1863, 1969-1972, 2081, 2105, 2128, 2169, 2176, 2178-2179, 2186, 2199, 2224-2237, 2298, 2332, 2350, 2360, 2387, 2389, 2393, 2402, 2427, 2455, 2584, 2595, 2599, 2616, 2619, 2691, 2722, 2739, 2767, 2791, 2797-\>2808, 2808-\>2819, 2844, 2860, 2946-\>2951, 2970-2974, 2979-2983, 3114, 3118-\>3131, 3131-\>3143, 3143-\>3150, 3186-3192, 3217-3223, 3264-\>3274, 3274-\>3287, 3402-3406, 3471-3480, 3590, 3609 |
| src/rememberstack/spine/lifecycle.py                                                          |      164 |        8 |       22 |        3 |     93.0% |389-390, 420, 448-454 |
| src/rememberstack/spine/migrations/\_\_init\_\_.py                                            |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/\_helpers.py                                               |      123 |        9 |       72 |        5 |     91.8% |104-109, 130-132, 139-\>143, 145-\>147, 152 |
| src/rememberstack/spine/migrations/env.py                                                     |       29 |        5 |        6 |        3 |     77.1% |13-\>16, 24, 29-37, 56 |
| src/rememberstack/spine/migrations/versions/\_\_init\_\_.py                                   |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0001\_extensions\_enums.py                |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0002\_infrastructure\_registries.py       |       18 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0003\_entities\_evaluation\_e0\_e1.py     |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0004\_claims\_facts\_evidence.py          |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0005\_projection\_knowledge\_retrieval.py |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p0\_02\_0006\_partitions\_views.py                |       18 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p2\_06\_0007\_invalidated\_outcome.py             |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p3\_01\_0008\_document\_version\_target.py        |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p3\_05\_0009\_reconcile\_stage.py                 |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p3\_07\_0010\_lifecycle\_eval\_suite.py           |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p4\_01\_0011\_survivor\_view\_rewrite.py          |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p6\_02\_0012\_knowledge\_compile\_recovery.py     |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p6\_04\_0013\_knowledge\_writer\_ledger.py        |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p6\_05\_0014\_knowledge\_planner\_runtime.py      |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p6\_06\_0015\_authored\_dispatch\_runtime.py      |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p7\_02\_0016\_operational\_eval\_suite.py         |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p7\_05\_0017\_hard\_forget.py                     |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/observation\_adjudication.py                                          |      159 |       21 |       34 |       11 |     82.4% |132, 230-249, 257-276, 328-346, 443-\>315, 477-494, 513, 521-533, 550, 688, 693 |
| src/rememberstack/spine/operations.py                                                         |       76 |        1 |        4 |        1 |     97.5% |       151 |
| src/rememberstack/spine/projection.py                                                         |      140 |       17 |       10 |        0 |     86.0% |49-50, 276-283, 289-292, 330-331, 340-341, 352-353 |
| src/rememberstack/spine/readiness.py                                                          |       52 |        1 |       10 |        1 |     96.8% |        58 |
| src/rememberstack/spine/recipes.py                                                            |       48 |        3 |        4 |        0 |     90.4% |   396-398 |
| src/rememberstack/spine/resolver.py                                                           |      179 |       12 |       48 |       11 |     89.9% |215, 217, 225-\>227, 235-241, 300, 310-311, 315, 398, 412, 635, 640 |
| src/rememberstack/spine/review.py                                                             |      120 |        7 |       30 |        7 |     90.7% |118, 178, 248-\>259, 345-349, 396, 398, 649 |
| src/rememberstack/spine/settings.py                                                           |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/supersession.py                                                       |       97 |        6 |       26 |        5 |     91.1% |104, 222, 238, 263-273, 321 |
| src/rememberstack/spine/sync.py                                                               |       26 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/work\_ledger.py                                                       |      194 |        9 |       54 |       10 |     92.3% |70, 177, 241, 267, 271, 345, 356, 374, 415, 559-\>563 |
| src/rememberstack/surfaces/\_\_init\_\_.py                                                    |       14 |        2 |        0 |        0 |     85.7% |   106-107 |
| src/rememberstack/surfaces/cli.py                                                             |      252 |       49 |       42 |        8 |     79.3% |55-56, 60-61, 71-76, 103-108, 127-132, 137-139, 141-149, 159-161, 171-181, 199-200, 230-233, 240-241, 249-251, 266-268, 332-342 |
| src/rememberstack/surfaces/consumption\_skill.py                                              |       42 |        2 |        8 |        1 |     94.0% |    35, 67 |
| src/rememberstack/surfaces/graph\_queries.py                                                  |      202 |       14 |       52 |       10 |     90.6% |105-106, 223-224, 238, 242, 308, 312, 409-410, 429, 544-\>549, 566-\>568, 677, 684, 690 |
| src/rememberstack/surfaces/http\_api.py                                                       |      142 |        8 |       22 |        3 |     92.1% |199, 299, 307, 314-326, 358-359 |
| src/rememberstack/surfaces/mcp.py                                                             |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_engine.py                                                   |      286 |        9 |       60 |        8 |     95.1% |109-110, 161, 396, 456, 635, 710-\>712, 713, 973, 1007 |
| src/rememberstack/surfaces/recipe\_executor.py                                                |       97 |       12 |       40 |        7 |     83.2% |72, 111, 113, 116, 131, 144-150, 185, 187 |
| src/rememberstack/surfaces/recipe\_surface.py                                                 |       98 |       18 |       40 |        3 |     76.1% |62-68, 78-83, 176-\>184, 221, 234-242 |
| src/rememberstack/surfaces/remote\_mcp.py                                                     |       62 |       12 |       24 |        7 |     77.9% |57-58, 65, 71-72, 88, 98, 107, 113, 116, 121, 129 |
| src/rememberstack/surfaces/sdk.py                                                             |      132 |       17 |       34 |        7 |     84.3% |71, 114, 140-146, 154, 162, 181, 234, 236, 270, 330-331, 332-\>334, 337-338 |
| src/rememberstack/workers/\_\_init\_\_.py                                                     |       76 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/base.py                                                             |      107 |        1 |       18 |        1 |     98.4% |       110 |
| src/rememberstack/workers/e0.py                                                               |      178 |       11 |       12 |        2 |     92.1% |296-300, 522, 539, 541-547, 573-574, 581 |
| src/rememberstack/workers/e1.py                                                               |      126 |        2 |       14 |        2 |     97.1% |  236, 434 |
| src/rememberstack/workers/e2.py                                                               |      203 |        7 |       72 |        9 |     94.2% |186, 405, 447, 451, 492, 505, 543-\>541, 568-\>570, 667 |
| src/rememberstack/workers/e3.py                                                               |      133 |        6 |       36 |        5 |     93.5% |247-250, 357, 382, 406, 419 |
| src/rememberstack/workers/forget.py                                                           |      130 |       17 |       26 |        2 |     85.3% |118-123, 179, 191-197, 289-297 |
| src/rememberstack/workers/knowledge\_authored.py                                              |       77 |        5 |       16 |        3 |     91.4% |55, 109, 117, 128-129 |
| src/rememberstack/workers/knowledge\_driver.py                                                |      295 |       53 |       88 |       14 |     77.3% |166, 247-258, 290, 495-511, 515, 562-\>564, 591-611, 625, 629, 633, 641-647, 654-672, 696, 699-700, 702, 705-706, 708, 734 |
| src/rememberstack/workers/knowledge\_fact\_sheet.py                                           |       41 |        2 |        2 |        1 |     93.0% |    31, 57 |
| src/rememberstack/workers/knowledge\_planner.py                                               |      135 |       13 |       20 |        7 |     87.1% |73, 107, 175, 198, 206, 208, 213, 242-250, 273-274 |
| src/rememberstack/workers/knowledge\_writer.py                                                |      156 |       12 |       22 |       11 |     87.1% |74, 94, 140, 174, 191, 196, 198, 297, 334, 340, 342, 363 |
| src/rememberstack/workers/operations.py                                                       |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/p1.py                                                               |       80 |        1 |       12 |        1 |     97.8% |       233 |
| src/rememberstack/workers/p2.py                                                               |      194 |       11 |       44 |       12 |     90.3% |268-270, 282-\>299, 322-325, 326-\>330, 341, 424-\>453, 430, 437, 446, 464, 477, 512-\>516 |
| src/rememberstack/workers/p2\_analytics.py                                                    |      104 |        3 |       16 |        1 |     96.7% |212, 232-233 |
| src/rememberstack/workers/p3.py                                                               |      247 |        5 |       68 |        5 |     96.8% |122-127, 226-\>228, 333-\>335, 337-\>341, 564, 671 |
| src/rememberstack/workers/reconcile.py                                                        |      149 |        8 |       38 |       11 |     89.8% |118, 193, 236, 241, 275-\>267, 277, 311, 312-\>317, 321, 394-\>405, 507 |
| src/rememberstack/workers/sync.py                                                             |       70 |        0 |       18 |        1 |     98.9% |  108-\>85 |
| **TOTAL**                                                                                     | **13169** | **1056** | **2772** |  **508** | **89.2%** |           |


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