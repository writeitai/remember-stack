# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/writeitai/remember-stack/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                                          |    Stmts |     Miss |   Branch |   BrPart |     Cover |   Missing |
|---------------------------------------------------------------------------------------------- | -------: | -------: | -------: | -------: | --------: | --------: |
| src/remember/\_\_init\_\_.py                                                                  |       41 |        5 |        0 |        0 |     87.8% |     45-49 |
| src/remember/\_\_main\_\_.py                                                                  |        5 |        5 |        2 |        0 |      0.0% |      3-10 |
| src/remember/cli.py                                                                           |      626 |      103 |      130 |       22 |     81.6% |66-\>96, 120-131, 146-148, 203-210, 216-219, 222-228, 246-247, 298-300, 308, 311, 318, 353-358, 366-368, 374-382, 390-392, 404-409, 427-429, 444-449, 455-457, 468-473, 491, 493-503, 532-540, 553-558, 560-568, 570-578, 597-601, 610, 629-630, 681-684, 742, 794-\>799, 826-828, 843-845, 874-882, 1302-1306 |
| src/remember/client.py                                                                        |      509 |      106 |      170 |       35 |     74.5% |185, 201, 229-230, 236-237, 249-260, 266-271, 275-279, 283-285, 309, 312, 331, 333, 345-359, 375, 377, 392, 400-442, 469-\>471, 472, 474, 475-\>477, 496-\>498, 512, 530, 557, 658, 764, 778-780, 783, 830-\>832, 857, 1009-1010, 1021, 1028, 1030, 1033, 1056, 1067, 1110, 1199, 1221-1222, 1224, 1226, 1231-1232, 1253-1254, 1260, 1276-1277, 1280-\>1282, 1283-1284, 1290-1299, 1306, 1309-1311 |
| src/remember/connection.py                                                                    |      186 |       10 |       46 |        5 |     93.5% |101, 103, 268-269, 331, 350-351, 353-\>355, 390, 393-394 |
| src/remember/credentials.py                                                                   |      196 |       32 |       40 |       12 |     78.8% |67-69, 110, 118, 120, 161-164, 165-\>174, 168-169, 181, 234, 236, 249-\>exit, 258, 279-282, 286, 296, 306-308, 328-336, 373-374 |
| src/remember/errors.py                                                                        |       26 |        0 |        0 |        0 |    100.0% |           |
| src/remember/issuer.py                                                                        |      260 |       35 |       56 |       12 |     84.5% |105-\>112, 110, 114, 134, 145-146, 167-168, 192, 203, 284, 294, 372-373, 377, 382-383, 413, 423-426, 435, 438-441, 453-454, 458, 465, 478-479, 514-517 |
| src/remember/login.py                                                                         |      201 |       22 |       50 |        6 |     88.8% |73-78, 134-\>136, 143-\>145, 215-216, 221-222, 245-247, 264, 267, 278-279, 299, 316-318, 320, 327-329 |
| src/remember/mcp\_bridge.py                                                                   |      241 |       24 |       94 |       18 |     87.5% |99, 102-106, 108-115, 140, 196, 199-\>201, 202-\>204, 254, 297, 312, 316, 321-\>exit, 338-339, 349, 373-\>375, 379, 382-\>exit, 389-390, 394-\>393, 403, 420, 423, 428, 435-436 |
| src/remember/mcp\_engine.py                                                                   |      126 |       10 |       40 |        8 |     89.2% |190, 211, 217, 219, 244, 249, 254, 280, 283-284 |
| src/remember/mcp\_http.py                                                                     |      209 |       31 |       46 |        7 |     85.1% |83, 105, 133, 164-165, 170-172, 228-229, 235-236, 244-247, 249-257, 306-307, 351-357, 362-372 |
| src/remember/mcp\_tools/\_\_init\_\_.py                                                       |       26 |        0 |        0 |        0 |    100.0% |           |
| src/remember/mcp\_tools/\_definitions.py                                                      |       80 |        0 |        8 |        0 |    100.0% |           |
| src/remember/mcp\_tools/\_errors.py                                                           |       89 |        5 |       42 |        5 |     92.4% |91, 171, 181, 192, 194 |
| src/remember/mcp\_tools/\_memory.py                                                           |      325 |       44 |      116 |       23 |     83.0% |98, 100-108, 111, 181, 288-293, 305, 463, 521, 542, 558, 683-684, 745, 760-763, 780, 794, 812-813, 823, 834, 845, 935, 947, 954-955, 961, 1057, 1061, 1065, 1079 |
| src/remember/mcp\_tools/\_query.py                                                            |       95 |       11 |       56 |        7 |     85.4% |37, 42, 55, 111, 112-\>133, 124-130, 193 |
| src/remember/mcp\_tools/\_validate.py                                                         |       39 |        2 |       10 |        1 |     93.9% |     59-60 |
| src/remember/mime.py                                                                          |       10 |        0 |        2 |        0 |    100.0% |           |
| src/remember/models.py                                                                        |      492 |       16 |       28 |        4 |     94.2% |34, 71-74, 334-336, 475-477, 521-523, 693, 695 |
| src/remember/query\_sandbox/\_\_init\_\_.py                                                   |        0 |        0 |        0 |        0 |    100.0% |           |
| src/remember/query\_sandbox/errors.py                                                         |       35 |        0 |        0 |        0 |    100.0% |           |
| src/remember/query\_sandbox/result.py                                                         |       70 |        0 |        0 |        0 |    100.0% |           |
| src/remember/setup.py                                                                         |      420 |       44 |      136 |       11 |     89.0% |151-156, 319-321, 408, 417-418, 428-431, 453-454, 457-461, 481-483, 485-489, 548-549, 553-554, 602, 666-675, 713-714, 720-721, 825-827 |
| src/rememberstack/\_\_init\_\_.py                                                             |        9 |        5 |        0 |        0 |     44.4% |      8-12 |
| src/rememberstack/adapters/\_\_init\_\_.py                                                    |       56 |       12 |       14 |        6 |     74.3% |101-105, 111-113, 115-119, 121-125, 127-129, 131-133 |
| src/rememberstack/adapters/bounded\_postgres\_read.py                                         |       56 |        5 |       18 |        6 |     85.1% |22, 24, 50, 71-\>78, 74, 84 |
| src/rememberstack/adapters/codex\_subscription.py                                             |      196 |       23 |       60 |       12 |     85.5% |80, 82, 84, 86, 185, 245, 248, 318, 349-350, 357-358, 366-\>370, 383, 420-426, 438, 449, 453 |
| src/rememberstack/adapters/codex\_writer.py                                                   |       82 |        3 |       20 |        3 |     94.1% |172, 203, 215 |
| src/rememberstack/adapters/converters/\_\_init\_\_.py                                         |       30 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/adapters/converters/image\_ocr\_description.py                              |      449 |       34 |      120 |       21 |     90.0% |137, 148, 198-\>exit, 431, 488-489, 519-520, 527, 621, 676, 684, 694, 699, 722, 725, 743, 746, 749, 752-760, 803, 817, 847-\>850, 890-\>892, 1020, 1040-1041, 1045, 1052-1053, 1057 |
| src/rememberstack/adapters/converters/markitdown.py                                           |       29 |        2 |        0 |        0 |     93.1% |     46-47 |
| src/rememberstack/adapters/converters/mistral\_ocr.py                                         |      263 |       20 |       90 |       17 |     89.0% |188-189, 204, 252-\>262, 320-\>323, 389, 406, 409, 434, 439-440, 447-\>449, 479, 484, 508, 518, 521-522, 524-525, 554-\>553, 556-\>555, 577-579 |
| src/rememberstack/adapters/generation\_recorder.py                                            |      134 |       17 |       14 |        4 |     85.8% |193-196, 211-231, 269-270, 288-\>290, 290-\>295, 299, 302-\>309 |
| src/rememberstack/adapters/managed/\_\_init\_\_.py                                            |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/managed/composite\_auth.py                                         |       19 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/adapters/managed/perimeter\_trust.py                                        |      205 |       16 |       72 |       12 |     89.9% |122, 145, 169, 241, 246, 303-\>305, 340-341, 387, 418-419, 421, 428-429, 442, 466, 477, 485-\>exit |
| src/rememberstack/adapters/managed/signed\_token\_auth.py                                     |      135 |        1 |       62 |        1 |     99.0% |       169 |
| src/rememberstack/adapters/openrouter.py                                                      |      592 |       45 |      204 |       25 |     91.0% |75-76, 82, 202, 561-564, 661-673, 717, 726, 740, 761-762, 763-\>772, 768-\>772, 771, 818-\>821, 879, 930, 952-953, 960-\>962, 966-967, 969, 982-\>984, 991-992, 1079-1080, 1101-1102, 1104, 1108-\>1110, 1111-\>1120, 1124, 1149-1150, 1152, 1171, 1271, 1276-1277, 1281, 1323 |
| src/rememberstack/adapters/postgres\_p1.py                                                    |      455 |       79 |      142 |       38 |     77.7% |232, 272, 286, 318, 347, 380, 418, 458, 505, 593-594, 642, 650-660, 662-663, 714-\>726, 727-728, 785-795, 797-798, 840, 860, 869, 894, 909, 957, 1004, 1027, 1130, 1153, 1218-1230, 1339, 1344, 1351-1355, 1456-1458, 1475, 1492-1513, 1533, 1553-1559, 1591-1606, 1635, 1643, 1657 |
| src/rememberstack/adapters/routed.py                                                          |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/\_\_init\_\_.py                                           |       23 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/control\_plane\_spend\_lease.py                           |       65 |       19 |       22 |        9 |     67.8% |41, 43, 45, 49-50, 75, 87, 94-97, 104, 106, 109-110, 112, 120-121, 126 |
| src/rememberstack/adapters/selfhost/forget.py                                                 |       43 |        1 |       10 |        1 |     96.2% |        19 |
| src/rememberstack/adapters/selfhost/git.py                                                    |      100 |        4 |       34 |        6 |     92.5% |55, 75-\>127, 154, 269, 313, 337-\>335 |
| src/rememberstack/adapters/selfhost/hashed\_bearer\_auth.py                                   |       47 |        8 |       12 |        4 |     79.7% |27, 46, 64-65, 70, 73-74, 76 |
| src/rememberstack/adapters/selfhost/managed\_metering.py                                      |       45 |        8 |       12 |        3 |     80.7% |42-43, 52-\>exit, 59, 66-67, 78-79, 81 |
| src/rememberstack/adapters/selfhost/minio.py                                                  |      118 |       15 |       32 |        8 |     82.0% |127, 129, 140, 143-148, 161, 188, 218, 230, 234, 256-258, 265 |
| src/rememberstack/adapters/selfhost/mounts.py                                                 |       98 |        4 |       22 |        3 |     94.2% |132, 163-\>186, 184-185, 252 |
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
| src/rememberstack/adapters/typesafe.py                                                        |       75 |       11 |       20 |        2 |     82.1% |100-104, 124-125, 152, 158-160 |
| src/rememberstack/adapters/vertex.py                                                          |      320 |       43 |       96 |       16 |     84.4% |210-245, 516-\>487, 569-570, 572, 580-581, 589-590, 657, 671, 680, 682, 689, 696, 699, 701, 739, 748-\>736, 750-\>752, 754-756, 784, 806 |
| src/rememberstack/client.py                                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/\_\_init\_\_.py                                                        |       92 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/assured\_operation\_linter.py                                          |       33 |        4 |       18 |        4 |     84.3% |58, 81, 88, 93 |
| src/rememberstack/core/blockizer.py                                                           |       93 |        5 |       36 |        5 |     92.2% |202, 217, 246, 252, 254 |
| src/rememberstack/core/chunker.py                                                             |       73 |        0 |       20 |        0 |    100.0% |           |
| src/rememberstack/core/concise\_adjudication.py                                               |      305 |        8 |      142 |       16 |     94.6% |74, 83-\>81, 87-\>85, 91-\>89, 132, 145, 212-\>222, 302-\>305, 342-\>347, 356, 410, 416, 419-\>424, 455-\>457, 499, 594 |
| src/rememberstack/core/consumption\_skill.py                                                  |       74 |        0 |        8 |        0 |    100.0% |           |
| src/rememberstack/core/context\_references.py                                                 |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/conversion.py                                                          |       62 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/core/core\_manifest.py                                                      |       21 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/embedding\_input\_policy.py                                            |      191 |       13 |       74 |        9 |     89.4% |140-\>149, 159, 215-216, 258, 284-290, 310-\>312, 332, 333-\>335, 341 |
| src/rememberstack/core/entity\_profile\_input.py                                              |        5 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/extension\_packs.py                                                    |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/fact\_application.py                                                   |       44 |        6 |       28 |        6 |     83.3% |52, 62, 64, 76, 78, 82 |
| src/rememberstack/core/fact\_label.py                                                         |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/fact\_windows.py                                                       |       71 |        4 |       38 |        4 |     92.7% |55, 57, 69, 146 |
| src/rememberstack/core/forget.py                                                              |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_authored.py                                                 |      170 |       25 |       72 |       15 |     83.5% |47-\>58, 116-117, 141, 160, 167, 172-173, 182, 185, 193-196, 212, 216-217, 223, 233-234, 239, 245-246, 250, 252, 255, 261-\>263 |
| src/rememberstack/core/knowledge\_compile.py                                                  |      106 |       11 |       52 |        7 |     86.1% |39, 44, 129, 131, 170-172, 184-186, 202 |
| src/rememberstack/core/knowledge\_fact\_sheet.py                                              |       77 |        5 |       30 |        5 |     90.7% |40, 82, 101, 149, 218 |
| src/rememberstack/core/knowledge\_hashing.py                                                  |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_planner.py                                                  |       33 |        4 |       14 |        4 |     83.0% |33, 35, 48, 50 |
| src/rememberstack/core/knowledge\_writer.py                                                   |       71 |        1 |       30 |        1 |     98.0% |        24 |
| src/rememberstack/core/open\_query\_prose.py                                                  |       20 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/ranking.py                                                             |       66 |        8 |       20 |        6 |     83.7% |60, 125, 127, 166, 178-179, 186, 197 |
| src/rememberstack/core/section\_snap.py                                                       |       63 |        3 |       26 |        3 |     93.3% |122, 177, 203 |
| src/rememberstack/core/selection\_references.py                                               |      147 |       11 |       50 |       11 |     88.8% |113, 122, 134, 229, 231, 242, 263, 344, 347, 351, 354 |
| src/rememberstack/core/source\_passages.py                                                    |      168 |       20 |       66 |       13 |     83.3% |54, 140-142, 155, 175, 201, 258, 283, 288, 298, 300, 303, 337, 340-341, 348-350, 393 |
| src/rememberstack/core/storage\_routing.py                                                    |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/structure\_skeleton.py                                                 |      261 |        6 |       90 |        7 |     96.3% |343, 345, 450, 533-\>532, 601, 603, 666 |
| src/rememberstack/core/temporal.py                                                            |       78 |        5 |       38 |        5 |     91.4% |55, 88, 120, 143, 156 |
| src/rememberstack/core/text\_metering.py                                                      |       51 |        2 |       20 |        2 |     94.4% |    72, 92 |
| src/rememberstack/eval/\_\_init\_\_.py                                                        |       17 |        2 |        2 |        1 |     84.2% |  147, 156 |
| src/rememberstack/eval/consumption.py                                                         |       43 |        2 |        8 |        2 |     92.2% |    76, 79 |
| src/rememberstack/eval/contradiction.py                                                       |       45 |        1 |       10 |        1 |     96.4% |       111 |
| src/rememberstack/eval/harness.py                                                             |       41 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/eval/lifecycle.py                                                           |       57 |        2 |        8 |        2 |     93.8% |   71, 178 |
| src/rememberstack/eval/operational\_scale.py                                                  |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/eval/resolution.py                                                          |       76 |        1 |       16 |        1 |     97.8% |       311 |
| src/rememberstack/eval/retrieval\_spikes.py                                                   |       16 |       16 |        0 |        0 |      0.0% |      3-41 |
| src/rememberstack/eval/skeleton.py                                                            |       73 |        7 |       26 |        7 |     85.9% |100, 109, 128, 151, 186, 189, 220 |
| src/rememberstack/llm/\_\_init\_\_.py                                                         |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/\_\_init\_\_.py                                                       |      377 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/adjudication.py                                                       |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/assured\_operations.py                                                |       70 |        1 |        6 |        1 |     97.4% |       133 |
| src/rememberstack/model/auth.py                                                               |       42 |        0 |        8 |        0 |    100.0% |           |
| src/rememberstack/model/blocks.py                                                             |       28 |        2 |        4 |        2 |     87.5% |    41, 47 |
| src/rememberstack/model/chunks.py                                                             |       85 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/claims.py                                                             |      131 |        1 |        8 |        1 |     98.6% |       179 |
| src/rememberstack/model/client.py                                                             |       22 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/clustering.py                                                         |       22 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/component\_version.py                                                 |       65 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/model/concise\_adjudication.py                                              |       61 |        6 |       16 |        6 |     84.4% |52, 114, 126, 131, 139, 141 |
| src/rememberstack/model/consumption.py                                                        |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/conversion.py                                                         |      165 |       11 |       22 |        2 |     89.8% |95, 187, 280-282, 309-311, 329-331 |
| src/rememberstack/model/deployment.py                                                         |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/documents.py                                                          |       51 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/envelope.py                                                           |      202 |        2 |        8 |        2 |     98.1% |  106, 638 |
| src/rememberstack/model/evaluation.py                                                         |       27 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/fact\_application.py                                                  |       61 |        6 |       16 |        6 |     84.4% |32, 97, 109, 116, 124, 126 |
| src/rememberstack/model/fact\_windows.py                                                      |       60 |        4 |       28 |        4 |     90.9% |80, 85, 90, 119 |
| src/rememberstack/model/forget.py                                                             |       63 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/git.py                                                                |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/knowledge.py                                                          |      369 |       24 |       38 |       14 |     88.7% |216, 255, 339, 351, 370, 382, 412, 571, 588, 616-627, 639-641, 670, 685, 706, 744, 753 |
| src/rememberstack/model/knowledge\_authored.py                                                |      135 |        4 |        8 |        3 |     95.1% |29, 101, 112, 192 |
| src/rememberstack/model/knowledge\_planner.py                                                 |      212 |       15 |       32 |       11 |     88.5% |47, 106, 139, 145, 160, 189, 194, 196, 238, 275-277, 291, 312, 379 |
| src/rememberstack/model/lifecycle.py                                                          |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/metering.py                                                           |       89 |        2 |        6 |        2 |     95.8% |   82, 133 |
| src/rememberstack/model/model\_provider.py                                                    |       45 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/mounts.py                                                             |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/object\_store.py                                                      |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/occurrence\_provenance.py                                             |      179 |       11 |       48 |        8 |     91.6% |147-148, 162, 165-\>171, 192, 266, 285-286, 336, 381, 399, 417 |
| src/rememberstack/model/operational\_scale.py                                                 |       25 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/operations.py                                                         |       47 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/processing.py                                                         |       86 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/queue.py                                                              |       45 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/relations.py                                                          |       38 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/resolution.py                                                         |       37 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/model/retrieval\_spikes.py                                                  |       26 |        7 |        2 |        0 |     67.9% | 49-56, 61 |
| src/rememberstack/model/sections.py                                                           |      184 |        3 |       10 |        3 |     96.9% |287, 291, 297 |
| src/rememberstack/model/spend\_lease.py                                                       |       42 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/telemetry.py                                                          |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/\_\_init\_\_.py                                                       |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/auth.py                                                               |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/connector.py                                                          |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/cost\_meter.py                                                        |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/forget.py                                                             |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/git.py                                                                |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/metering.py                                                           |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/model\_provider.py                                                    |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/mounts.py                                                             |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/object\_store.py                                                      |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/p1\_index.py                                                          |       52 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/postgres\_read.py                                                     |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/profile\_refresher.py                                                 |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/purge.py                                                              |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/queue.py                                                              |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/systemone.py                                                          |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/telemetry.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/profiles/\_\_init\_\_.py                                                    |       10 |        7 |        4 |        0 |     21.4% |     14-22 |
| src/rememberstack/profiles/selfhost.py                                                        |      738 |      278 |      142 |       21 |     60.6% |232, 267, 272, 276, 280, 335, 354, 483-484, 581, 598, 619, 650, 660, 668, 676-692, 715, 743-773, 786-796, 833-836, 871, 873, 878-989, 1145, 1150-1152, 1169-1174, 1182-1190, 1209-1222, 1226-1248, 1268-1270, 1274-1289, 1299-1303, 1320-1516, 1525-1527, 1532-1601, 1609, 1613, 1624-1625, 1690-1698, 1718-1726, 1765-1767, 1803 |
| src/rememberstack/profiles/selfhost\_forget.py                                                |       64 |       64 |        2 |        0 |      0.0% |     3-176 |
| src/rememberstack/profiles/selfhost\_operations.py                                            |       45 |        4 |        0 |        0 |     91.1% | 47, 72-74 |
| src/rememberstack/spine/\_\_init\_\_.py                                                       |       53 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/admission.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/apply\_fact\_decision.py                                              |      135 |       12 |       36 |        3 |     88.9% |48, 76, 229-254 |
| src/rememberstack/spine/assured\_operations.py                                                |       88 |        3 |        8 |        1 |     95.8% | 40-41, 93 |
| src/rememberstack/spine/backfill.py                                                           |       28 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/catalog\_contract.py                                                  |      155 |       22 |       70 |       22 |     80.4% |453, 508, 527, 555, 568, 609, 622, 637, 659, 683, 700, 711, 731, 787, 827, 829, 831, 833, 835, 837, 839, 891 |
| src/rememberstack/spine/chunk\_catalog.py                                                     |       75 |        2 |       12 |        2 |     95.4% |   39, 216 |
| src/rememberstack/spine/claim\_catalog.py                                                     |      140 |       12 |       48 |        8 |     87.2% |158-160, 176, 196, 247, 255, 317, 334-336, 582 |
| src/rememberstack/spine/clustering.py                                                         |      290 |       14 |      108 |       17 |     92.2% |67, 142, 202-\>180, 248, 318, 387, 468-\>447, 534-\>523, 615, 617, 628, 633, 676, 679, 698, 799, 804 |
| src/rememberstack/spine/component\_versions.py                                                |       56 |        3 |       12 |        3 |     91.2% |102, 119, 187 |
| src/rememberstack/spine/consumption.py                                                        |       20 |        1 |        2 |        1 |     90.9% |        32 |
| src/rememberstack/spine/cost\_export.py                                                       |      212 |       15 |       40 |        8 |     90.9% |96, 192, 283, 302-303, 336, 344-345, 359, 377, 387-388, 390, 397, 430 |
| src/rememberstack/spine/deployment\_bootstrap.py                                              |       84 |        1 |       20 |        1 |     98.1% |       245 |
| src/rememberstack/spine/document\_bindings.py                                                 |      105 |        7 |       34 |        7 |     89.9% |91-\>109, 112-\>114, 118, 152, 162, 169, 174-175, 220 |
| src/rememberstack/spine/document\_catalog.py                                                  |      184 |        4 |       30 |        5 |     95.8% |277, 347, 376, 581, 592-\>618 |
| src/rememberstack/spine/document\_inventory.py                                                |       39 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/entity\_eligibility.py                                                |       16 |        2 |        6 |        2 |     81.8% |    44, 56 |
| src/rememberstack/spine/entity\_registry.py                                                   |       49 |        6 |        4 |        1 |     83.0% |64-\>84, 121, 125-131 |
| src/rememberstack/spine/extension\_packs.py                                                   |       40 |        1 |       16 |        1 |     96.4% |       119 |
| src/rememberstack/spine/fact\_adjudication.py                                                 |      259 |       23 |       94 |       25 |     86.4% |265, 284, 290-\>256, 326, 374-375, 378, 380, 440, 453, 471, 474, 478, 482, 542-\>547, 543-\>542, 553-\>593, 565-\>569, 566-\>565, 569-\>593, 607-608, 639, 734, 749, 761, 763, 794, 815-816 |
| src/rememberstack/spine/fact\_application\_inputs.py                                          |      119 |        7 |       36 |        8 |     90.3% |65, 93, 186, 263, 292, 428, 519, 523-\>520 |
| src/rememberstack/spine/fact\_applications.py                                                 |      119 |        7 |       30 |        7 |     90.6% |54, 74, 96, 196, 204, 263, 536 |
| src/rememberstack/spine/fact\_catalog.py                                                      |      147 |       25 |       14 |        2 |     80.7% |49, 64, 105, 291-\>293, 316, 327-336, 342-343, 361-362, 386-405, 411-423, 454-465 |
| src/rememberstack/spine/fact\_graph\_contract.py                                              |       17 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/spine/forget.py                                                             |      230 |       46 |       52 |       16 |     73.8% |51, 65-75, 87-92, 101, 120-121, 182, 200-207, 211-215, 222, 252, 256, 266, 368-369, 392, 413, 439-452, 485, 497-514, 537, 554, 728 |
| src/rememberstack/spine/graph\_catalog.py                                                     |      107 |       12 |       30 |       10 |     83.9% |373-376, 388, 528, 532-\>534, 537, 596, 633-634, 637, 654-655, 667 |
| src/rememberstack/spine/knowledge.py                                                          |     1263 |      117 |      462 |       98 |     86.8% |153, 168, 239, 249, 277, 283-\>exit, 296, 307, 319, 323, 377, 419, 453, 514-519, 557, 625-634, 649, 668, 696-\>692, 763, 777, 888, 929, 959, 1050, 1084, 1186, 1237, 1273, 1282, 1337, 1357, 1388, 1441-1444, 1471, 1476, 1478, 1480, 1536, 1538, 1540, 1542, 1544, 1561, 1568, 1572, 1603, 1614, 1616-\>1634, 1673, 1789, 1818, 1836, 1861-1867, 1973-1976, 2085, 2109, 2132, 2173, 2180, 2182-2183, 2190, 2203, 2228-2241, 2302, 2336, 2354, 2364, 2391, 2393, 2397, 2406, 2431, 2459, 2588, 2599, 2603, 2620, 2623, 2695, 2726, 2743, 2771, 2795, 2801-\>2812, 2812-\>2823, 2848, 2864, 2941-\>2946, 2970-2974, 3089, 3093-\>3106, 3106-\>3118, 3118-\>3125, 3161, 3184-3190, 3231-\>3241, 3241-\>3254, 3369-3373, 3434-3443, 3553, 3572 |
| src/rememberstack/spine/lifecycle.py                                                          |      240 |        9 |       26 |        2 |     95.1% |176-185, 485, 513-519 |
| src/rememberstack/spine/managed\_metering.py                                                  |      216 |       26 |       40 |        8 |     86.7% |153, 188-201, 216-221, 231, 233, 239-265, 268, 277-278, 414-\>344, 443-458, 463-\>439 |
| src/rememberstack/spine/migrations/\_\_init\_\_.py                                            |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/\_helpers.py                                               |      151 |        8 |       88 |        4 |     94.1% |163-168, 189-191, 198-\>202, 204-\>206 |
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
| src/rememberstack/spine/migrations/versions/p9\_21\_0042\_ingest\_principal.py                |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_22\_0043\_document\_entity\_bindings.py       |       20 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_23\_0044\_drop\_generic\_identifier\_guard.py |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_24\_0045\_managed\_text\_metering.py          |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_25\_0046\_document\_inventory\_order.py       |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_26\_0047\_canonical\_bounds.py                |       11 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_27\_0048\_query\_space\_canonical\_bounds.py  |       41 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_28\_0049\_context\_operation\_names.py        |       27 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_29\_0050\_no\_route\_defer\_reason.py         |       17 |        4 |        0 |        0 |     76.5% |     88-97 |
| src/rememberstack/spine/migrations/versions/p9\_30\_0051\_mutable\_fact\_windows.py           |       40 |        3 |       10 |        2 |     90.0% |367, 377, 430 |
| src/rememberstack/spine/migrations/versions/p9\_31\_0052\_multi\_span\_claim\_evidence.py     |       28 |        1 |        4 |        1 |     93.8% |        77 |
| src/rememberstack/spine/migrations/versions/p9\_32\_0053\_source\_reference\_context.py       |       18 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_33\_0054\_perimeter\_state.py                 |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_34\_0055\_embedding\_model\_indexes.py        |       14 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/observation\_adjudication.py                                          |       98 |       27 |       18 |        5 |     65.5% |132, 148, 161, 201, 209-221, 251, 256, 261, 271, 281-296, 301-304, 319 |
| src/rememberstack/spine/operations.py                                                         |       76 |        1 |        4 |        1 |     97.5% |       151 |
| src/rememberstack/spine/perimeter\_state.py                                                   |       24 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/postgres\_graph\_sql.py                                               |       12 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/profile\_convergence.py                                               |       24 |        6 |        0 |        0 |     75.0% |30-37, 48-55 |
| src/rememberstack/spine/profile\_refresher.py                                                 |      247 |       15 |       84 |       15 |     90.9% |111, 123-\>99, 125, 190, 201, 221, 278, 301-302, 353, 395, 486, 579, 648, 671, 707-\>681, 728 |
| src/rememberstack/spine/projection.py                                                         |       90 |       18 |        8 |        2 |     75.5% |74-75, 114-124, 146-153, 159-162, 201-202, 213-214, 240 |
| src/rememberstack/spine/query\_space/\_\_init\_\_.py                                          |       40 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/ast\_serializer.py                                       |       27 |        4 |       10 |        2 |     83.8% |77-78, 82, 102 |
| src/rememberstack/spine/query\_space/canonical.py                                             |       71 |        2 |       32 |        1 |     97.1% |   89, 137 |
| src/rememberstack/spine/query\_space/catalog.py                                               |       31 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/deletion\_matrix.py                                      |       80 |        2 |       18 |        1 |     96.9% |  564, 570 |
| src/rememberstack/spine/query\_space/manifest.py                                              |      190 |       18 |       48 |       13 |     87.0% |159, 499, 515, 561, 650, 656, 664, 734, 814, 831, 838-841, 846, 851, 862, 873, 1013, 1015 |
| src/rememberstack/spine/query\_space/quarantine.py                                            |       20 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/source\_definitions.py                                   |      108 |        4 |       32 |        4 |     94.3% |154, 206, 209, 231 |
| src/rememberstack/spine/rank\_embed\_cache.py                                                 |      105 |       11 |       30 |        9 |     85.2% |44, 46, 48, 50, 89, 101, 125-127, 148-\>152, 175, 181 |
| src/rememberstack/spine/readiness.py                                                          |      170 |       25 |       56 |       10 |     81.0% |68, 410-418, 434, 477, 479-\>493, 486-\>479, 494, 518, 520, 528, 530, 534-537, 540-543 |
| src/rememberstack/spine/resolver.py                                                           |      324 |       14 |       88 |       10 |     94.2% |444, 473, 790-793, 801-808, 818-823, 937, 978, 1147, 1159, 1164, 1191 |
| src/rememberstack/spine/review.py                                                             |      197 |       14 |       58 |       14 |     89.0% |140, 249, 304, 345, 385-\>396, 482-486, 533, 535, 603, 607, 626, 641, 670, 927 |
| src/rememberstack/spine/selection\_catalog.py                                                 |       59 |        3 |       10 |        3 |     91.3% |211, 220, 223 |
| src/rememberstack/spine/settings.py                                                           |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/supersession.py                                                       |       13 |        1 |        0 |        0 |     92.3% |        36 |
| src/rememberstack/spine/surface\_cost.py                                                      |      127 |       24 |       10 |        3 |     78.8% |85-86, 116, 144-146, 149-154, 179-181, 185-198, 242-243 |
| src/rememberstack/spine/sync.py                                                               |       32 |        0 |        2 |        1 |     97.1% |   90-\>98 |
| src/rememberstack/spine/work\_ledger.py                                                       |      410 |       35 |      140 |       37 |     86.9% |75, 192, 212, 214, 232, 293, 295, 309, 355, 357, 389, 399, 421, 467, 469, 479, 504, 506, 576, 578, 588, 602-\>617, 679, 683, 748, 767, 816, 827, 845, 890, 952, 1097, 1108, 1118, 1287, 1358, 1472-\>1476 |
| src/rememberstack/surfaces/\_\_init\_\_.py                                                    |       14 |        2 |        0 |        0 |     85.7% |   101-102 |
| src/rememberstack/surfaces/consumption\_skill.py                                              |       42 |        3 |        8 |        2 |     90.0% |35, 67, 86 |
| src/rememberstack/surfaces/cost\_export\_api.py                                               |      112 |       37 |       24 |        1 |     63.2% |121-125, 146-162, 167-192, 201 |
| src/rememberstack/surfaces/direct\_admission.py                                               |      134 |        0 |       46 |        0 |    100.0% |           |
| src/rememberstack/surfaces/graph\_queries.py                                                  |      358 |       37 |      114 |       30 |     85.0% |66, 84, 86, 88, 117, 169-176, 247, 278, 306, 317, 366, 368, 415-416, 426-\>431, 429, 432, 441, 475-\>478, 629, 635, 667-672, 730, 734, 743, 792-794, 820, 858, 883, 906, 998, 1000, 1026-1027 |
| src/rememberstack/surfaces/http\_api.py                                                       |      671 |       44 |      160 |       10 |     92.3% |332, 454, 529, 884, 1051-1056, 1126-1132, 1139-1167, 1284-\>1288, 1329, 1335-1336, 1396-1399, 1478, 1480, 1516, 1528, 1544, 1568-1569, 1637, 1801-\>1805, 1869-\>1872, 1887-1888, 1901, 1922-1926, 1934-1935, 1941-1942 |
| src/rememberstack/surfaces/mcp.py                                                             |      150 |       19 |       36 |        5 |     82.8% |95, 249, 264-265, 327, 333, 342-412, 417 |
| src/rememberstack/surfaces/operation\_executor.py                                             |       39 |        2 |        8 |        2 |     91.5% |   75, 118 |
| src/rememberstack/surfaces/operation\_surface.py                                              |      124 |       18 |       60 |       10 |     81.5% |160, 172, 174, 181-183, 189-193, 202, 211-212, 229, 235, 238, 240 |
| src/rememberstack/surfaces/query\_engine.py                                                   |     1110 |      113 |      330 |       65 |     86.1% |232-233, 345, 364-369, 395, 439, 630, 708, 711, 713, 722, 774, 999-1000, 1021-1029, 1038, 1065-1066, 1101, 1112, 1131-1132, 1156, 1225-1231, 1664, 1728, 1779, 1801-1803, 1839, 1846, 1856-\>1858, 1859, 1897, 1904, 1915, 1954, 1968, 1982-\>1996, 2076, 2150-\>2152, 2153, 2431-2488, 2507-2567, 2590-\>2592, 2593, 2639, 2705, 2804-2821, 2826-\>2840, 2890-2900, 2959, 3046, 3050, 3080, 3120, 3139-3140, 3174, 3189-3197, 3211, 3213, 3221, 3235, 3259, 3285, 3287, 3291, 3301, 3328, 3330, 3348, 3383, 3490, 3533-3541, 3643, 3647, 4019 |
| src/rememberstack/surfaces/query\_sandbox/\_\_init\_\_.py                                     |       21 |       14 |        6 |        0 |     25.9% |     50-66 |
| src/rememberstack/surfaces/query\_sandbox/audit.py                                            |       92 |        7 |       14 |        4 |     89.6% |109-\>116, 153-154, 157-158, 178, 203, 207 |
| src/rememberstack/surfaces/query\_sandbox/bridge.py                                           |      257 |       27 |       80 |       12 |     87.8% |212, 220-221, 228, 274, 290, 304, 345, 347, 370-371, 494-495, 570-571, 590, 606, 631, 643-644, 737, 763, 846, 857-860 |
| src/rememberstack/surfaces/query\_sandbox/discovery.py                                        |      106 |        3 |       24 |        3 |     95.4% |117, 179, 227 |
| src/rememberstack/surfaces/query\_sandbox/errors.py                                           |        5 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/examples.py                                         |       64 |        1 |        6 |        1 |     97.1% |       444 |
| src/rememberstack/surfaces/query\_sandbox/executor.py                                         |      322 |       31 |       94 |       19 |     87.5% |109-110, 123, 126-127, 132, 140, 174-175, 180, 186, 193, 207-212, 321, 327, 338, 340, 342, 345-347, 499, 519, 538-543, 598, 753-756, 821-822 |
| src/rememberstack/surfaces/query\_sandbox/grammar.py                                          |      657 |       54 |      254 |       34 |     89.0% |238, 242, 247, 381, 406, 454, 558-\>557, 582, 599-600, 603-612, 635, 718, 724, 776, 780-781, 788, 798, 896, 902-911, 925-928, 934-\>931, 978-\>976, 1049-\>1051, 1069, 1093-\>1097, 1113-1114, 1117, 1143-\>1141, 1150, 1198, 1245-\>1251, 1281, 1287-1293, 1299-1300, 1304-1307, 1339, 1356, 1410 |
| src/rememberstack/surfaces/query\_sandbox/limits.py                                           |       23 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/nomination.py                                       |      257 |       28 |       90 |       14 |     87.3% |200-201, 206, 275-283, 290, 479-480, 544, 546, 548, 662-668, 674, 691-692, 729, 751-752, 760, 792-793, 816-\>818 |
| src/rememberstack/surfaces/query\_sandbox/open\_query.py                                      |       62 |        3 |       10 |        2 |     93.1% |74, 145, 203 |
| src/rememberstack/surfaces/query\_sandbox/result.py                                           |       70 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/saved\_queries.py                                   |      585 |       64 |      240 |       56 |     84.7% |205, 223, 286, 447, 497, 503, 508, 545, 590, 601, 609, 649, 737, 803, 809, 825, 875-\>885, 943, 964, 1048, 1078, 1101, 1274, 1360, 1547-\>1549, 1662, 1667, 1751, 1919, 1925, 1945, 1958, 1963, 1972-1983, 1990, 2000, 2006, 2016, 2023, 2040, 2046-2051, 2057, 2078, 2080, 2083, 2103, 2153, 2158, 2175-2180, 2183, 2187, 2191, 2201, 2236, 2253-\>2257, 2268, 2296-2298 |
| src/rememberstack/surfaces/route\_scope.py                                                    |       25 |        0 |       14 |        0 |    100.0% |           |
| src/rememberstack/workers/\_\_init\_\_.py                                                     |       81 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/base.py                                                             |      154 |        3 |       32 |        1 |     97.8% |217, 450-451 |
| src/rememberstack/workers/e0.py                                                               |      523 |       33 |      116 |       17 |     91.2% |299-\>310, 425-\>434, 436-445, 911, 1037-1054, 1114, 1198-\>1197, 1204, 1207, 1303, 1340-1341, 1382, 1447, 1529, 1564, 1625, 1643, 1658, 1677-1679, 1720-\>1725, 1726, 1733, 1741 |
| src/rememberstack/workers/e0\_summary.py                                                      |      392 |       43 |      118 |       22 |     85.3% |351-352, 354-358, 425, 482, 503, 521, 530, 552, 554-556, 570-571, 576, 587, 609, 628, 636, 651, 662, 666-682, 694-697, 707, 717, 906, 917, 928, 945, 957-967 |
| src/rememberstack/workers/e1.py                                                               |      227 |       31 |       76 |       14 |     81.2% |225, 251, 257-\>252, 277-\>261, 321, 335, 355, 381, 392-423, 454, 456, 491-\>493, 493-\>exit, 507-529, 556-559, 710 |
| src/rememberstack/workers/e2.py                                                               |      567 |       37 |      202 |       33 |     90.6% |441, 453, 580, 620, 649, 667, 774, 963, 1006-\>1013, 1009, 1017-1018, 1037, 1050, 1059, 1068, 1171, 1310-\>1309, 1323-\>1327, 1345, 1387, 1507-1508, 1509-\>1541, 1511-\>1541, 1515, 1520, 1534, 1536-\>1541, 1654, 1673, 1749, 1802, 1816, 1829, 1862-1865, 1904-1906, 1975, 2020 |
| src/rememberstack/workers/e3.py                                                               |      240 |       19 |       64 |       13 |     88.8% |224, 239, 253, 281, 342, 390, 422, 464-479, 488, 523, 534, 538, 624, 639-\>658 |
| src/rememberstack/workers/extraction\_references.py                                           |       94 |        7 |       30 |        6 |     89.5% |70-71, 90, 197, 232, 248, 250 |
| src/rememberstack/workers/forget.py                                                           |      129 |       16 |       28 |        2 |     86.0% |125-130, 186, 198-203, 302-310 |
| src/rememberstack/workers/knowledge\_authored.py                                              |       77 |        5 |       16 |        3 |     91.4% |55, 109, 117, 128-129 |
| src/rememberstack/workers/knowledge\_driver.py                                                |      295 |       53 |       88 |       14 |     77.3% |166, 247-258, 290, 495-511, 515, 562-\>564, 591-611, 625, 629, 633, 641-647, 654-672, 696, 699-700, 702, 705-706, 708, 734 |
| src/rememberstack/workers/knowledge\_fact\_sheet.py                                           |       41 |        2 |        2 |        1 |     93.0% |    31, 57 |
| src/rememberstack/workers/knowledge\_planner.py                                               |      135 |       13 |       20 |        7 |     87.1% |73, 107, 175, 198, 206, 208, 213, 242-250, 273-274 |
| src/rememberstack/workers/knowledge\_writer.py                                                |      156 |       12 |       22 |       11 |     87.1% |80, 100, 146, 180, 197, 202, 204, 303, 340, 346, 348, 369 |
| src/rememberstack/workers/lane\_checkpoints.py                                                |       24 |        2 |        0 |        0 |     91.7% |     45-46 |
| src/rememberstack/workers/operations.py                                                       |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/p1.py                                                               |      107 |        3 |       22 |        4 |     94.6% |177, 184, 290-\>302, 309 |
| src/rememberstack/workers/p3.py                                                               |      264 |        4 |       80 |        2 |     98.3% |122-127, 350-\>366, 710 |
| src/rememberstack/workers/reconcile.py                                                        |      240 |       13 |       56 |       12 |     91.6% |142, 223-224, 231, 276, 330-331, 361, 366, 400-\>392, 402, 436, 437-\>442, 544-\>555, 581-\>585, 643, 884 |
| src/rememberstack/workers/section\_orientation.py                                             |       48 |        4 |       18 |        4 |     87.9% |46, 83, 95, 97 |
| src/rememberstack/workers/sync.py                                                             |       70 |        0 |       18 |        1 |     98.9% |  108-\>85 |
| **TOTAL**                                                                                     | **33021** | **2963** | **8370** | **1452** | **88.3%** |           |


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