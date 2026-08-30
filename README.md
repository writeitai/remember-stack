# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/writeitai/remember-stack/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                                          |    Stmts |     Miss |   Branch |   BrPart |     Cover |   Missing |
|---------------------------------------------------------------------------------------------- | -------: | -------: | -------: | -------: | --------: | --------: |
| src/rememberstack/\_\_init\_\_.py                                                             |        6 |        2 |        0 |        0 |     66.7% |       8-9 |
| src/rememberstack/adapters/\_\_init\_\_.py                                                    |       28 |        8 |       10 |        4 |     68.4% |45-49, 55-57, 59-61, 63-65 |
| src/rememberstack/adapters/bounded\_postgres\_read.py                                         |       56 |        5 |       18 |        6 |     85.1% |22, 24, 50, 71-\>78, 74, 84 |
| src/rememberstack/adapters/codex\_writer.py                                                   |       82 |        3 |       20 |        3 |     94.1% |172, 203, 215 |
| src/rememberstack/adapters/converters/\_\_init\_\_.py                                         |       27 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/adapters/converters/markitdown.py                                           |       29 |        2 |        0 |        0 |     93.1% |     46-47 |
| src/rememberstack/adapters/converters/mistral\_ocr.py                                         |      263 |       21 |       90 |       20 |     87.8% |188-189, 204, 252-\>262, 278, 296-\>308, 320-\>323, 371-\>373, 389, 406, 409, 434, 439-440, 447-\>449, 479, 484, 508, 518, 521-522, 524-525, 554-\>553, 556-\>555, 577-579 |
| src/rememberstack/adapters/openrouter.py                                                      |      341 |       30 |      114 |       13 |     90.1% |64-65, 71, 153, 349-352, 412, 474-477, 481, 500-506, 516, 521-522, 526, 574-575, 596-597, 599, 603-\>605, 606-\>615, 619, 644-645, 647, 717 |
| src/rememberstack/adapters/postgres\_p1.py                                                    |      435 |       79 |      138 |       38 |     76.8% |147, 187, 201, 233, 262, 295, 327, 361, 408, 496-497, 545, 553-563, 565-566, 617-\>629, 630-631, 688-698, 700-701, 743, 763, 772, 797, 812, 860, 907, 930, 1033, 1056, 1121-1133, 1225, 1230, 1237-1241, 1342-1344, 1361, 1378-1399, 1419, 1439-1445, 1477-1492, 1521, 1529, 1543 |
| src/rememberstack/adapters/selfhost/\_\_init\_\_.py                                           |       22 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/control\_plane\_spend\_lease.py                           |       60 |       20 |       20 |        9 |     63.8% |40, 42, 44, 48-49, 55, 63, 75, 82-85, 92, 94, 97-98, 100, 108-109, 114 |
| src/rememberstack/adapters/selfhost/forget.py                                                 |       43 |        1 |       10 |        1 |     96.2% |        19 |
| src/rememberstack/adapters/selfhost/git.py                                                    |       98 |        6 |       32 |        8 |     89.2% |55, 75-\>127, 154, 184-185, 199-\>227, 263, 307, 331-\>329 |
| src/rememberstack/adapters/selfhost/hashed\_bearer\_auth.py                                   |       47 |        8 |       12 |        4 |     79.7% |27, 46, 64-65, 70, 73-74, 76 |
| src/rememberstack/adapters/selfhost/minio.py                                                  |      113 |       14 |       30 |        7 |     82.5% |123, 125, 136, 139-144, 177, 207, 219, 223, 245-247, 254 |
| src/rememberstack/adapters/selfhost/mounts.py                                                 |       94 |        3 |       16 |        2 |     95.5% |150-\>173, 171-172, 239 |
| src/rememberstack/adapters/selfhost/object\_store.py                                          |       60 |        2 |       26 |        2 |     95.3% |  102, 104 |
| src/rememberstack/adapters/selfhost/projection.py                                             |       35 |        3 |       12 |        3 |     87.2% |50, 58, 71 |
| src/rememberstack/adapters/selfhost/queue.py                                                  |       65 |        1 |       10 |        2 |     96.0% |111-\>118, 135 |
| src/rememberstack/adapters/selfhost/telemetry.py                                              |       43 |        7 |        6 |        1 |     79.6% | 58, 63-68 |
| src/rememberstack/adapters/selfhost/watcher.py                                                |       31 |        1 |       10 |        1 |     95.1% |        39 |
| src/rememberstack/adapters/sentry.py                                                          |       74 |        9 |       28 |        6 |     85.3% |119-125, 152-\>160, 154-\>160, 157, 166, 169, 172 |
| src/rememberstack/adapters/testing/\_\_init\_\_.py                                            |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/cost\_meter.py                                             |        4 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/model\_provider.py                                         |       35 |        1 |        4 |        1 |     94.9% |        50 |
| src/rememberstack/adapters/testing/profile\_refresher.py                                      |       15 |        2 |        0 |        0 |     86.7% |     25-26 |
| src/rememberstack/adapters/testing/queue.py                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/telemetry.py                                               |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/client.py                                                                   |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/\_\_init\_\_.py                                                        |       79 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/assured\_operation\_linter.py                                          |       33 |        4 |       18 |        4 |     84.3% |58, 81, 85, 90 |
| src/rememberstack/core/blockizer.py                                                           |       93 |        5 |       36 |        5 |     92.2% |202, 217, 246, 252, 254 |
| src/rememberstack/core/chunker.py                                                             |       73 |        0 |       20 |        0 |    100.0% |           |
| src/rememberstack/core/consumption\_skill.py                                                  |       74 |        0 |        8 |        0 |    100.0% |           |
| src/rememberstack/core/conversion.py                                                          |       51 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/core/core\_manifest.py                                                      |       21 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/embedding\_input\_policy.py                                            |      191 |       13 |       74 |        9 |     89.4% |140-\>149, 159, 215-216, 258, 284-290, 310-\>312, 332, 333-\>335, 341 |
| src/rememberstack/core/entity\_profile\_input.py                                              |        5 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/extension\_packs.py                                                    |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/fact\_label.py                                                         |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/forget.py                                                              |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_authored.py                                                 |      170 |       25 |       72 |       15 |     83.5% |47-\>58, 116-117, 141, 160, 167, 172-173, 182, 185, 193-196, 212, 216-217, 223, 233-234, 239, 245-246, 250, 252, 255, 261-\>263 |
| src/rememberstack/core/knowledge\_compile.py                                                  |      106 |       11 |       52 |        7 |     86.1% |39, 44, 129, 131, 170-172, 184-186, 202 |
| src/rememberstack/core/knowledge\_fact\_sheet.py                                              |       71 |        3 |       30 |        3 |     94.1% |37, 102, 150 |
| src/rememberstack/core/knowledge\_hashing.py                                                  |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_planner.py                                                  |       33 |        4 |       14 |        4 |     83.0% |33, 35, 48, 50 |
| src/rememberstack/core/knowledge\_writer.py                                                   |       71 |        1 |       30 |        1 |     98.0% |        24 |
| src/rememberstack/core/open\_query\_prose.py                                                  |       18 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/ranking.py                                                             |       66 |        8 |       20 |        6 |     83.7% |60, 125, 127, 166, 178-179, 186, 197 |
| src/rememberstack/core/section\_snap.py                                                       |       63 |        3 |       26 |        3 |     93.3% |122, 177, 203 |
| src/rememberstack/core/storage\_routing.py                                                    |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/structure\_skeleton.py                                                 |      261 |        7 |       90 |        8 |     95.7% |341, 343, 345, 450, 533-\>532, 601, 603, 666 |
| src/rememberstack/eval/\_\_init\_\_.py                                                        |       17 |        2 |        2 |        1 |     84.2% |  147, 156 |
| src/rememberstack/eval/consumption.py                                                         |       43 |        2 |        8 |        2 |     92.2% |    76, 79 |
| src/rememberstack/eval/contradiction.py                                                       |       45 |        1 |       10 |        1 |     96.4% |       111 |
| src/rememberstack/eval/harness.py                                                             |       41 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/eval/lifecycle.py                                                           |       57 |        2 |        8 |        2 |     93.8% |   71, 178 |
| src/rememberstack/eval/operational\_scale.py                                                  |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/eval/resolution.py                                                          |       76 |        1 |       16 |        1 |     97.8% |       311 |
| src/rememberstack/eval/retrieval\_spikes.py                                                   |       16 |       16 |        0 |        0 |      0.0% |      3-41 |
| src/rememberstack/eval/skeleton.py                                                            |       73 |        7 |       26 |        7 |     85.9% |96, 105, 124, 147, 181, 184, 215 |
| src/rememberstack/llm/\_\_init\_\_.py                                                         |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/\_\_init\_\_.py                                                       |      350 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/adjudication.py                                                       |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/assured\_operations.py                                                |       70 |        1 |        6 |        1 |     97.4% |       130 |
| src/rememberstack/model/auth.py                                                               |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/blocks.py                                                             |       28 |        2 |        4 |        2 |     87.5% |    41, 47 |
| src/rememberstack/model/chunks.py                                                             |       81 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/claims.py                                                             |      109 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/client.py                                                             |       68 |        4 |       16 |        1 |     89.3% |   201-204 |
| src/rememberstack/model/clustering.py                                                         |       22 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/component\_version.py                                                 |       65 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/model/consumption.py                                                        |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/conversion.py                                                         |      159 |       11 |       22 |        2 |     89.5% |95, 187, 280-282, 309-311, 329-331 |
| src/rememberstack/model/deployment.py                                                         |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/documents.py                                                          |       43 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/envelope.py                                                           |      193 |        2 |        8 |        2 |     98.0% |  103, 587 |
| src/rememberstack/model/evaluation.py                                                         |       27 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/forget.py                                                             |       63 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/git.py                                                                |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/knowledge.py                                                          |      366 |       24 |       38 |       14 |     88.6% |215, 254, 338, 350, 369, 381, 411, 568, 585, 613-624, 636-638, 667, 682, 703, 741, 750 |
| src/rememberstack/model/knowledge\_authored.py                                                |      135 |        4 |        8 |        3 |     95.1% |29, 101, 112, 192 |
| src/rememberstack/model/knowledge\_planner.py                                                 |      212 |       15 |       32 |       11 |     88.5% |47, 106, 139, 145, 160, 189, 194, 196, 238, 275-277, 291, 312, 379 |
| src/rememberstack/model/lifecycle.py                                                          |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/model\_provider.py                                                    |       45 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/mounts.py                                                             |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/object\_store.py                                                      |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/operational\_scale.py                                                 |       25 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/operations.py                                                         |       46 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/processing.py                                                         |       90 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/queue.py                                                              |       45 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/relations.py                                                          |       27 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/resolution.py                                                         |       32 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/retrieval\_spikes.py                                                  |       26 |        7 |        2 |        0 |     67.9% | 49-56, 61 |
| src/rememberstack/model/sections.py                                                           |      183 |        3 |       10 |        3 |     96.9% |278, 282, 288 |
| src/rememberstack/model/spend\_lease.py                                                       |        6 |        0 |        0 |        0 |    100.0% |           |
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
| src/rememberstack/ports/p1\_index.py                                                          |       52 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/postgres\_read.py                                                     |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/profile\_refresher.py                                                 |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/purge.py                                                              |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/queue.py                                                              |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/telemetry.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/profiles/\_\_init\_\_.py                                                    |       10 |        7 |        4 |        0 |     21.4% |     14-22 |
| src/rememberstack/profiles/selfhost.py                                                        |      566 |      233 |       96 |       14 |     56.3% |151, 168, 187, 247-248, 278, 295, 316, 347, 357, 365, 373-389, 412, 440-470, 483-493, 530-533, 568, 570, 575-667, 792, 799-801, 816-838, 858-860, 864-879, 883-887, 896-1087, 1130-1132, 1137-1184, 1192, 1196, 1207-1208, 1315 |
| src/rememberstack/profiles/selfhost\_forget.py                                                |       64 |       64 |        2 |        0 |      0.0% |     3-176 |
| src/rememberstack/profiles/selfhost\_operations.py                                            |       39 |        4 |        0 |        0 |     89.7% | 47, 61-63 |
| src/rememberstack/spine/\_\_init\_\_.py                                                       |       51 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/admission.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/assured\_operations.py                                                |       83 |        3 |        8 |        1 |     95.6% | 37-38, 90 |
| src/rememberstack/spine/backfill.py                                                           |       28 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/catalog\_contract.py                                                  |      151 |       20 |       68 |       21 |     81.3% |442, 497, 516, 544, 557, 598, 611, 626, 648, 664, 675, 695, 750-\>762, 790, 792, 794, 796, 798, 800, 802, 854 |
| src/rememberstack/spine/chunk\_catalog.py                                                     |       65 |        2 |       12 |        2 |     94.8% |   39, 186 |
| src/rememberstack/spine/claim\_catalog.py                                                     |       80 |        8 |       16 |        4 |     85.4% |98-\>107, 130-140, 153, 161, 212 |
| src/rememberstack/spine/clustering.py                                                         |      288 |       14 |      108 |       17 |     92.2% |66, 141, 201-\>179, 247, 317, 386, 458-\>437, 524-\>513, 605, 607, 618, 623, 666, 669, 688, 789, 794 |
| src/rememberstack/spine/component\_versions.py                                                |       56 |        3 |       12 |        3 |     91.2% |102, 119, 187 |
| src/rememberstack/spine/consumption.py                                                        |       20 |        1 |        2 |        1 |     90.9% |        32 |
| src/rememberstack/spine/cost\_export.py                                                       |      212 |       15 |       40 |        8 |     90.9% |96, 192, 283, 302-303, 336, 344-345, 359, 377, 387-388, 390, 397, 430 |
| src/rememberstack/spine/deployment\_bootstrap.py                                              |       75 |        0 |       14 |        0 |    100.0% |           |
| src/rememberstack/spine/document\_catalog.py                                                  |      154 |        4 |       22 |        5 |     94.9% |147, 217, 246, 451, 462-\>488 |
| src/rememberstack/spine/entity\_eligibility.py                                                |       16 |        2 |        6 |        2 |     81.8% |    44, 56 |
| src/rememberstack/spine/entity\_registry.py                                                   |       49 |        6 |        4 |        1 |     83.0% |64-\>84, 121, 125-131 |
| src/rememberstack/spine/extension\_packs.py                                                   |       40 |        1 |       16 |        1 |     96.4% |       119 |
| src/rememberstack/spine/fact\_catalog.py                                                      |      164 |       36 |       16 |        1 |     75.0% |124-171, 322-\>324, 368-377, 383-384, 402-403, 427-446, 452-464, 474-485, 495-506 |
| src/rememberstack/spine/forget.py                                                             |      198 |       42 |       46 |       15 |     71.7% |49, 63-73, 85-90, 99, 118-119, 180, 198-205, 209-213, 219-226, 250, 254, 264, 366-367, 390, 411, 437-450, 480, 508, 525, 584 |
| src/rememberstack/spine/graph\_catalog.py                                                     |      117 |       12 |       40 |       10 |     86.0% |369-372, 384, 524, 528-\>530, 533, 592, 629-630, 633, 650-651, 663 |
| src/rememberstack/spine/knowledge.py                                                          |     1261 |      117 |      462 |       98 |     86.8% |153, 168, 239, 249, 277, 283-\>exit, 296, 307, 319, 323, 377, 419, 453, 514-519, 542, 610-619, 634, 653, 681-\>677, 748, 762, 873, 914, 944, 1035, 1069, 1171, 1222, 1258, 1267, 1322, 1342, 1373, 1426-1429, 1456, 1461, 1463, 1465, 1521, 1523, 1525, 1527, 1529, 1546, 1553, 1557, 1588, 1599, 1601-\>1619, 1658, 1774, 1803, 1821, 1846-1852, 1958-1961, 2070, 2094, 2117, 2158, 2165, 2167-2168, 2175, 2188, 2213-2226, 2287, 2321, 2339, 2349, 2376, 2378, 2382, 2391, 2416, 2444, 2573, 2584, 2588, 2605, 2608, 2680, 2711, 2728, 2756, 2780, 2786-\>2797, 2797-\>2808, 2833, 2849, 2926-\>2931, 2955-2959, 3074, 3078-\>3091, 3091-\>3103, 3103-\>3110, 3146, 3169-3175, 3216-\>3226, 3226-\>3239, 3354-3358, 3419-3428, 3538, 3557 |
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
| src/rememberstack/spine/migrations/versions/p9\_14\_0035\_drop\_entity\_type.py               |       37 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_15\_0036\_global\_resolution\_eval.py         |       24 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_16\_0037\_entity\_profile\_projection.py      |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_17\_0038\_postgres19\_live\_graph.py          |       74 |        0 |        8 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_18\_0039\_graph\_entity\_provenance\_plan.py  |       11 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_19\_0040\_graph\_tenant\_planner\_settings.py |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_20\_0041\_resolution\_uncertainty.py          |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/observation\_adjudication.py                                          |      246 |       44 |       72 |       19 |     77.7% |148, 162-163, 165, 239, 346-367, 375-396, 448-474, 538-586, 645-\>435, 681-702, 721, 729-741, 802-821, 1038, 1043, 1057-1058, 1076, 1078, 1085-1089 |
| src/rememberstack/spine/operations.py                                                         |       76 |        1 |        4 |        1 |     97.5% |       151 |
| src/rememberstack/spine/postgres\_graph\_sql.py                                               |       12 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/profile\_convergence.py                                               |       24 |        6 |        0 |        0 |     75.0% |30-37, 48-55 |
| src/rememberstack/spine/profile\_refresher.py                                                 |      242 |       14 |       84 |       14 |     91.4% |109, 121-\>97, 123, 188, 199, 219, 276, 299-300, 351, 393, 484, 576, 668, 690-\>678, 711 |
| src/rememberstack/spine/projection.py                                                         |       90 |       18 |        8 |        2 |     75.5% |74-75, 114-124, 146-153, 159-162, 201-202, 213-214, 240 |
| src/rememberstack/spine/query\_space/\_\_init\_\_.py                                          |       40 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/ast\_serializer.py                                       |       27 |        4 |       10 |        2 |     83.8% |77-78, 82, 102 |
| src/rememberstack/spine/query\_space/canonical.py                                             |       71 |        2 |       32 |        1 |     97.1% |   89, 137 |
| src/rememberstack/spine/query\_space/catalog.py                                               |       31 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/deletion\_matrix.py                                      |       80 |        2 |       18 |        1 |     96.9% |  560, 566 |
| src/rememberstack/spine/query\_space/manifest.py                                              |      189 |       18 |       48 |       13 |     86.9% |159, 478, 489, 535, 624, 630, 638, 708, 788, 805, 812-815, 820, 825, 836, 847, 987, 989 |
| src/rememberstack/spine/query\_space/quarantine.py                                            |       20 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/source\_definitions.py                                   |      104 |        4 |       32 |        4 |     94.1% |142, 190, 193, 215 |
| src/rememberstack/spine/rank\_embed\_cache.py                                                 |      105 |       11 |       30 |        9 |     85.2% |44, 46, 48, 50, 89, 101, 125-127, 148-\>152, 175, 181 |
| src/rememberstack/spine/readiness.py                                                          |      166 |       25 |       54 |       12 |     79.5% |68, 178-\>180, 389-397, 410-\>414, 413, 456, 458-\>472, 465-\>458, 473, 497, 499, 507, 509, 513-516, 519-522 |
| src/rememberstack/spine/resolver.py                                                           |      313 |       18 |       94 |       13 |     92.4% |310, 328-334, 340, 542, 651-654, 662-669, 679-684, 732, 810, 851, 1041, 1053, 1058, 1072 |
| src/rememberstack/spine/review.py                                                             |      153 |        8 |       38 |        8 |     91.6% |137, 241, 323, 354-\>365, 451-455, 502, 504, 757 |
| src/rememberstack/spine/settings.py                                                           |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/supersession.py                                                       |      126 |        9 |       38 |        8 |     89.6% |105, 197, 246, 262, 287-297, 358, 559, 561 |
| src/rememberstack/spine/surface\_cost.py                                                      |      125 |       24 |       10 |        3 |     78.5% |83-84, 114, 142-144, 147-152, 177-179, 183-196, 240-241 |
| src/rememberstack/spine/sync.py                                                               |       26 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/work\_ledger.py                                                       |      365 |       32 |      122 |       34 |     86.4% |70, 177, 241, 265, 267, 281, 327, 329, 361, 371, 393, 439, 441, 451, 464, 476, 478, 548, 550, 560, 574-\>589, 651, 655, 729, 740, 758, 803, 865, 966, 1009, 1020, 1174, 1209, 1322-\>1326 |
| src/rememberstack/surfaces/\_\_init\_\_.py                                                    |       14 |        2 |        0 |        0 |     85.7% |   121-122 |
| src/rememberstack/surfaces/cli.py                                                             |      518 |       99 |       92 |       16 |     80.8% |58, 66-67, 82-87, 128-133, 154-159, 167-169, 171-179, 187-189, 201-206, 224-226, 241-246, 252-254, 265-270, 288, 290-300, 320-328, 341-346, 348-356, 358-366, 381-385, 394, 413-414, 444-447, 454-455, 463-465, 480-482, 508-509, 567-569, 574-\>580, 599, 603-605, 627-629, 637-639, 648-649, 652-654, 726-736 |
| src/rememberstack/surfaces/consumption\_skill.py                                              |       42 |        3 |        8 |        2 |     90.0% |35, 67, 86 |
| src/rememberstack/surfaces/cost\_export\_api.py                                               |      112 |       37 |       24 |        1 |     63.2% |121-125, 146-162, 167-192, 201 |
| src/rememberstack/surfaces/credentials.py                                                     |      112 |       13 |       24 |        7 |     82.4% |82, 97-\>99, 106-108, 114, 119-120, 135, 147-150, 158, 159-\>exit |
| src/rememberstack/surfaces/device\_login.py                                                   |      123 |       25 |       30 |        9 |     73.9% |89, 115, 125-126, 132-133, 141-155, 167-168, 201, 205-206, 208, 234-235, 237-\>239 |
| src/rememberstack/surfaces/graph\_queries.py                                                  |      358 |       34 |      114 |       30 |     86.0% |66, 84, 86, 88, 117, 169-176, 247, 278, 306, 317, 366, 368, 415-416, 426-\>431, 429, 432, 441, 475-\>478, 629, 635, 671-\>658, 730, 734, 743, 792-794, 820, 858, 883, 906, 998, 1000, 1026-1027 |
| src/rememberstack/surfaces/http\_api.py                                                       |      384 |       51 |       78 |       10 |     84.6% |222, 321, 376, 486-492, 503-509, 593-596, 617-622, 692-698, 705-733, 811, 817-818, 866, 868, 888, 927-928, 1003, 1005-1007, 1015, 1018-1019, 1032, 1052-1056, 1062-1070 |
| src/rememberstack/surfaces/mcp.py                                                             |       72 |        2 |       18 |        2 |     95.6% |   70, 171 |
| src/rememberstack/surfaces/mcp\_memory\_tools.py                                              |      383 |       55 |      160 |       33 |     82.0% |292, 294-302, 305, 372, 417, 484, 488, 498, 509, 517, 672, 730, 751, 762, 884-885, 946, 961-964, 981, 995, 1003, 1015-1016, 1026, 1037, 1048, 1140, 1152, 1159-1160, 1166, 1227, 1351, 1373, 1389-1399, 1406-\>1418, 1458, 1473, 1477, 1481, 1495 |
| src/rememberstack/surfaces/operation\_executor.py                                             |       39 |        2 |        8 |        2 |     91.5% |   75, 118 |
| src/rememberstack/surfaces/operation\_surface.py                                              |      131 |       18 |       66 |       11 |     82.2% |108-\>110, 165, 177, 179, 186-188, 194-198, 207, 216-217, 234, 240, 243, 245 |
| src/rememberstack/surfaces/query\_engine.py                                                   |      999 |      106 |      298 |       61 |     85.4% |198-199, 311, 330-335, 354, 594, 672, 675, 677, 686, 738, 963-964, 985-993, 1002, 1029-1030, 1065, 1076, 1095-1096, 1120, 1189-1195, 1542, 1606, 1679-1681, 1717, 1724, 1734-\>1736, 1737, 1775, 1782, 1793, 1832, 1846, 1860-\>1874, 1954, 2028-\>2030, 2031, 2294-2351, 2370-2430, 2453-\>2455, 2456, 2502, 2557, 2656-2673, 2678-\>2692, 2742-2750, 2809, 2896, 2900, 2930, 2969-2977, 2991, 2993, 3001, 3015, 3039, 3065, 3067, 3069, 3077, 3104, 3106, 3124, 3159, 3266, 3309-3317, 3411, 3415 |
| src/rememberstack/surfaces/query\_sandbox/\_\_init\_\_.py                                     |       21 |       14 |        6 |        0 |     25.9% |     50-66 |
| src/rememberstack/surfaces/query\_sandbox/audit.py                                            |       92 |        7 |       14 |        4 |     89.6% |109-\>116, 153-154, 157-158, 178, 203, 207 |
| src/rememberstack/surfaces/query\_sandbox/bridge.py                                           |      257 |       27 |       80 |       12 |     87.8% |202, 210-211, 218, 264, 280, 294, 335, 337, 360-361, 484-485, 560-561, 580, 596, 621, 633-634, 727, 753, 836, 847-850 |
| src/rememberstack/surfaces/query\_sandbox/discovery.py                                        |      106 |        3 |       24 |        3 |     95.4% |117, 179, 227 |
| src/rememberstack/surfaces/query\_sandbox/errors.py                                           |       35 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/examples.py                                         |       62 |        1 |        6 |        1 |     97.1% |       460 |
| src/rememberstack/surfaces/query\_sandbox/executor.py                                         |      322 |       31 |       94 |       19 |     87.5% |109-110, 123, 126-127, 132, 140, 174-175, 180, 186, 193, 207-212, 321, 327, 338, 340, 342, 345-347, 499, 519, 538-543, 598, 753-756, 821-822 |
| src/rememberstack/surfaces/query\_sandbox/grammar.py                                          |      657 |       54 |      254 |       34 |     89.0% |236, 240, 245, 379, 404, 452, 556-\>555, 580, 597-598, 601-610, 633, 716, 722, 774, 778-779, 786, 796, 894, 900-909, 923-926, 932-\>929, 976-\>974, 1047-\>1049, 1067, 1091-\>1095, 1111-1112, 1115, 1141-\>1139, 1148, 1196, 1243-\>1249, 1279, 1285-1291, 1297-1298, 1302-1305, 1337, 1354, 1408 |
| src/rememberstack/surfaces/query\_sandbox/limits.py                                           |       23 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/mcp\_tools.py                                       |      141 |       45 |       84 |       11 |     63.6% |216, 219, 232, 264, 288, 289-\>310, 301-307, 325, 333, 350-424, 429-434, 439-448, 456-462, 522 |
| src/rememberstack/surfaces/query\_sandbox/nomination.py                                       |      257 |       28 |       90 |       14 |     87.3% |200-201, 206, 275-283, 290, 479-480, 544, 546, 548, 662-668, 674, 691-692, 729, 751-752, 760, 792-793, 816-\>818 |
| src/rememberstack/surfaces/query\_sandbox/open\_query.py                                      |       62 |        3 |       10 |        2 |     93.1% |74, 145, 203 |
| src/rememberstack/surfaces/query\_sandbox/result.py                                           |       70 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/saved\_queries.py                                   |      585 |       64 |      240 |       56 |     84.7% |205, 223, 286, 447, 497, 503, 508, 545, 590, 601, 609, 649, 737, 803, 809, 825, 875-\>885, 943, 964, 1048, 1078, 1101, 1274, 1360, 1547-\>1549, 1662, 1667, 1751, 1919, 1925, 1945, 1958, 1963, 1972-1983, 1990, 2000, 2006, 2016, 2023, 2040, 2046-2051, 2057, 2078, 2080, 2083, 2103, 2153, 2158, 2175-2180, 2183, 2187, 2191, 2201, 2236, 2253-\>2257, 2268, 2296-2298 |
| src/rememberstack/surfaces/remote\_mcp.py                                                     |      127 |       14 |       40 |        9 |     86.2% |189, 198, 212-213, 220, 226-227, 243, 253, 262, 268, 271, 276, 284 |
| src/rememberstack/surfaces/sdk.py                                                             |      297 |       49 |      102 |       25 |     77.4% |139, 182, 198, 240, 262, 264, 276-290, 306, 308, 333-375, 401-\>403, 404, 406, 407-\>409, 428-\>430, 444, 462, 474, 575, 630, 632, 666, 727-728, 747-\>749, 754-755, 766, 773, 775, 778, 804, 815 |
| src/rememberstack/workers/\_\_init\_\_.py                                                     |       80 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/base.py                                                             |      142 |        3 |       30 |        1 |     97.7% |203, 409-410 |
| src/rememberstack/workers/e0.py                                                               |      470 |       24 |      100 |       14 |     93.0% |819, 945-962, 1022, 1106-\>1105, 1112, 1115, 1211, 1248-1249, 1290, 1355, 1437, 1472, 1533, 1551, 1572-\>1577, 1578, 1585, 1593 |
| src/rememberstack/workers/e0\_summary.py                                                      |      392 |       43 |      118 |       22 |     85.3% |351-352, 354-358, 425, 482, 503, 521, 530, 552, 554-556, 570-571, 576, 587, 609, 628, 636, 651, 662, 666-682, 694-697, 707, 717, 906, 917, 928, 945, 957-967 |
| src/rememberstack/workers/e1.py                                                               |      226 |       31 |       76 |       14 |     81.1% |214, 240, 246-\>241, 266-\>250, 310, 324, 344, 370, 381-412, 443, 445, 480-\>482, 482-\>exit, 496-518, 545-548, 693 |
| src/rememberstack/workers/e2.py                                                               |      329 |       18 |      124 |       14 |     92.5% |259, 296, 360-364, 658, 700, 704, 816-817, 818-\>850, 820-\>850, 824, 829, 843, 845-\>850, 952, 971, 1071-1073, 1142 |
| src/rememberstack/workers/e3.py                                                               |      294 |       93 |       88 |       14 |     63.6% |160-166, 178, 192, 220, 260, 291-363, 380, 439, 442-445, 517, 540-555, 564, 595-600, 608, 612, 667-750, 815, 824-848 |
| src/rememberstack/workers/forget.py                                                           |      124 |       16 |       26 |        2 |     85.3% |110-115, 171, 183-188, 279-287 |
| src/rememberstack/workers/knowledge\_authored.py                                              |       77 |        5 |       16 |        3 |     91.4% |55, 109, 117, 128-129 |
| src/rememberstack/workers/knowledge\_driver.py                                                |      295 |       53 |       88 |       14 |     77.3% |166, 247-258, 290, 495-511, 515, 562-\>564, 591-611, 625, 629, 633, 641-647, 654-672, 696, 699-700, 702, 705-706, 708, 734 |
| src/rememberstack/workers/knowledge\_fact\_sheet.py                                           |       41 |        2 |        2 |        1 |     93.0% |    31, 57 |
| src/rememberstack/workers/knowledge\_planner.py                                               |      135 |       13 |       20 |        7 |     87.1% |73, 107, 175, 198, 206, 208, 213, 242-250, 273-274 |
| src/rememberstack/workers/knowledge\_writer.py                                                |      156 |       12 |       22 |       11 |     87.1% |74, 94, 140, 174, 191, 196, 198, 297, 334, 340, 342, 363 |
| src/rememberstack/workers/operations.py                                                       |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/p1.py                                                               |       86 |        1 |       10 |        1 |     97.9% |       255 |
| src/rememberstack/workers/p3.py                                                               |      247 |        4 |       68 |        4 |     97.5% |122-127, 226-\>228, 333-\>335, 337-\>341, 667 |
| src/rememberstack/workers/reconcile.py                                                        |      166 |       10 |       40 |       12 |     89.3% |125, 206-207, 214, 261, 266, 300-\>292, 302, 336, 337-\>342, 346, 424-\>435, 461-\>465, 567 |
| src/rememberstack/workers/section\_orientation.py                                             |       48 |        4 |       18 |        4 |     87.9% |46, 83, 95, 97 |
| src/rememberstack/workers/sync.py                                                             |       70 |        0 |       18 |        1 |     98.9% |  108-\>85 |
| **TOTAL**                                                                                     | **24303** | **2393** | **6082** | **1124** | **87.1%** |           |


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