# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/writeitai/remember-stack/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                                          |    Stmts |     Miss |   Branch |   BrPart |     Cover |   Missing |
|---------------------------------------------------------------------------------------------- | -------: | -------: | -------: | -------: | --------: | --------: |
| src/remember/\_\_init\_\_.py                                                                  |       82 |        5 |        0 |        0 |     93.9% |     86-90 |
| src/remember/\_\_main\_\_.py                                                                  |        5 |        5 |        2 |        0 |      0.0% |      3-10 |
| src/remember/cli.py                                                                           |      726 |      115 |      160 |       27 |     81.9% |75-\>107, 134-145, 160-162, 217-224, 230-233, 236-242, 260-261, 312-314, 322, 325, 332, 367-372, 380-382, 387-400, 412-417, 434-436, 451-456, 462-464, 475-480, 498, 500-510, 523-528, 547-557, 570-575, 577-585, 587-595, 615-619, 628, 652-653, 705-\>707, 713-\>715, 743-744, 776-779, 837, 857, 898-\>903, 922-928, 941-943, 958-960, 992-1000, 1630-1634 |
| src/remember/client.py                                                                        |      674 |       94 |      238 |       47 |     81.9% |220, 281-282, 288-289, 304-\>306, 306-\>308, 309, 311, 313, 315, 340, 362-\>364, 364-\>366, 367, 376-378, 407, 410, 429, 431, 443-457, 474, 476, 500, 509-553, 584-\>586, 587, 589, 590-\>592, 593, 624, 632-634, 717, 744, 858, 1004, 1012, 1015, 1042, 1072-\>1074, 1453, 1534-\>1538, 1540, 1632-1633, 1658, 1665, 1667, 1670, 1697, 1719, 1762, 1832, 1854-1855, 1857, 1859, 1864-1865, 1886-1887, 1909-1910, 1913-\>1915, 1916-1917, 1923-1932, 1939, 1942-1944 |
| src/remember/connection.py                                                                    |      189 |       10 |       46 |        5 |     93.6% |102, 104, 277-278, 340, 359-360, 362-\>364, 399, 402-403 |
| src/remember/credentials.py                                                                   |      207 |       35 |       46 |       13 |     77.9% |67-69, 110, 118, 120, 161-164, 165-\>174, 168-169, 181, 234, 236, 249-\>exit, 258, 275-277, 283-286, 293, 308, 318-320, 340-348, 385-386 |
| src/remember/errors.py                                                                        |       36 |        0 |        0 |        0 |    100.0% |           |
| src/remember/http\_routes.py                                                                  |        5 |        0 |        0 |        0 |    100.0% |           |
| src/remember/issuer.py                                                                        |      260 |       35 |       56 |       12 |     84.5% |105-\>112, 110, 114, 134, 145-146, 167-168, 192, 203, 284, 294, 372-373, 377, 382-383, 413, 423-426, 435, 438-441, 453-454, 458, 465, 478-479, 514-517 |
| src/remember/login.py                                                                         |      201 |       22 |       50 |        6 |     88.8% |73-78, 134-\>136, 143-\>145, 215-216, 221-222, 245-247, 264, 267, 278-279, 299, 316-318, 320, 327-329 |
| src/remember/mcp\_bridge.py                                                                   |      241 |       24 |       94 |       18 |     87.5% |99, 102-106, 108-115, 140, 196, 199-\>201, 202-\>204, 254, 297, 312, 316, 321-\>exit, 338-339, 349, 373-\>375, 379, 382-\>exit, 389-390, 394-\>393, 403, 420, 423, 428, 435-436 |
| src/remember/mcp\_engine.py                                                                   |      182 |       10 |       50 |        8 |     92.2% |296, 339, 345, 347, 372, 377, 382, 408, 411-412 |
| src/remember/mcp\_http.py                                                                     |      210 |       31 |       46 |        7 |     85.2% |83, 105, 139, 171-172, 177-179, 235-236, 242-243, 251-254, 256-264, 314-315, 365-375, 386-398 |
| src/remember/mcp\_tools/\_\_init\_\_.py                                                       |       42 |        0 |        0 |        0 |    100.0% |           |
| src/remember/mcp\_tools/\_definitions.py                                                      |      127 |        0 |        8 |        0 |    100.0% |           |
| src/remember/mcp\_tools/\_documents.py                                                        |       46 |        5 |        6 |        1 |     84.6% |106, 113-118 |
| src/remember/mcp\_tools/\_errors.py                                                           |       96 |        5 |       44 |        5 |     92.9% |92, 195, 205, 216, 218 |
| src/remember/mcp\_tools/\_memory.py                                                           |      358 |       46 |      126 |       23 |     84.1% |103, 105-113, 116, 190, 297-302, 314, 493, 579, 600, 616, 649-650, 666, 780-781, 842, 857-860, 877, 891, 909-910, 920, 931, 942, 1032, 1046, 1053-1054, 1152, 1156, 1160, 1174 |
| src/remember/mcp\_tools/\_query.py                                                            |       95 |        6 |       56 |        8 |     90.7% |37, 42, 55, 111, 112-\>133, 126, 129-\>133, 193 |
| src/remember/mcp\_tools/\_references.py                                                       |       48 |        8 |        8 |        1 |     76.8% |94, 116-126 |
| src/remember/mcp\_tools/\_sections.py                                                         |       47 |        8 |        8 |        1 |     76.4% |81, 98-108 |
| src/remember/mcp\_tools/\_tags.py                                                             |      113 |        6 |       30 |        3 |     93.7% |76, 78, 90, 110, 194-195 |
| src/remember/mcp\_tools/\_validate.py                                                         |       78 |        4 |       28 |        3 |     93.4% |48, 59, 111-112 |
| src/remember/mime.py                                                                          |       10 |        0 |        2 |        0 |    100.0% |           |
| src/remember/models.py                                                                        |      980 |       17 |       62 |        5 |     96.9% |37, 77-80, 423, 1248-1250, 1403-1405, 1472-1474, 1661, 1663 |
| src/remember/mount.py                                                                         |      120 |        6 |       28 |        1 |     95.3% |140-141, 144, 163-165 |
| src/remember/query\_sandbox/\_\_init\_\_.py                                                   |        0 |        0 |        0 |        0 |    100.0% |           |
| src/remember/query\_sandbox/errors.py                                                         |       35 |        0 |        0 |        0 |    100.0% |           |
| src/remember/query\_sandbox/result.py                                                         |       70 |        0 |        0 |        0 |    100.0% |           |
| src/remember/retrieval.py                                                                     |       29 |        0 |        4 |        0 |    100.0% |           |
| src/remember/setup.py                                                                         |      420 |       44 |      136 |       11 |     89.0% |151-156, 319-321, 408, 417-418, 428-431, 453-454, 457-461, 481-483, 485-489, 548-549, 553-554, 602, 666-675, 713-714, 720-721, 825-827 |
| src/rememberstack/\_\_init\_\_.py                                                             |        9 |        5 |        0 |        0 |     44.4% |      8-12 |
| src/rememberstack/adapters/\_\_init\_\_.py                                                    |       58 |       12 |       14 |        6 |     75.0% |105-109, 115-117, 119-123, 125-129, 131-133, 135-137 |
| src/rememberstack/adapters/bounded\_postgres\_read.py                                         |       81 |        8 |       26 |        8 |     85.0% |26, 28, 47-\>49, 58, 82, 116, 126, 143, 156 |
| src/rememberstack/adapters/codex\_subscription.py                                             |      196 |       23 |       60 |       12 |     85.5% |80, 82, 84, 86, 185, 245, 248, 318, 349-350, 357-358, 366-\>370, 383, 420-426, 438, 449, 453 |
| src/rememberstack/adapters/codex\_writer.py                                                   |       82 |        3 |       20 |        3 |     94.1% |172, 203, 215 |
| src/rememberstack/adapters/converters/\_\_init\_\_.py                                         |       72 |        2 |       14 |        2 |     95.3% |    43, 61 |
| src/rememberstack/adapters/converters/card.py                                                 |        5 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/converters/dataset.py                                              |      156 |        6 |       14 |        3 |     94.7% |82, 108, 234, 265-266, 283 |
| src/rememberstack/adapters/converters/email\_message.py                                       |       98 |        9 |       20 |        3 |     89.8% |57, 124-125, 127, 134-135, 168-169, 170-\>174, 172 |
| src/rememberstack/adapters/converters/image\_ocr\_description.py                              |      491 |       35 |      136 |       22 |     90.6% |156, 166, 177, 235-\>exit, 482, 549-550, 589-590, 597, 714, 769, 777, 787, 792, 815, 818, 836, 839, 842, 845-853, 896, 910, 940-\>943, 983-\>985, 1113, 1133-1134, 1138, 1145-1146, 1150 |
| src/rememberstack/adapters/converters/libreoffice.py                                          |       39 |        7 |        4 |        0 |     79.1% |29, 76-77, 84-90 |
| src/rememberstack/adapters/converters/markitdown.py                                           |       43 |        2 |        2 |        0 |     95.6% |     67-68 |
| src/rememberstack/adapters/converters/mistral\_ocr.py                                         |      348 |       30 |      122 |       21 |     87.9% |149, 168-173, 297, 332, 341-\>353, 356-357, 387-388, 408-410, 541-\>544, 610, 627, 630, 655, 660-661, 668-\>670, 700, 705, 729, 739, 742-743, 745-746, 775-\>774, 777-\>776, 798-800 |
| src/rememberstack/adapters/converters/notebook.py                                             |       84 |        5 |       30 |        7 |     89.5% |43, 68, 130, 133-\>131, 135-\>131, 137, 143-\>142, 145 |
| src/rememberstack/adapters/converters/office.py                                               |      181 |       11 |       36 |        6 |     92.2% |106, 112, 239-\>241, 249, 268, 272, 308-309, 341-343, 345 |
| src/rememberstack/adapters/converters/pdf.py                                                  |      227 |       24 |       54 |       11 |     86.8% |82, 95, 100, 171-172, 176-177, 181, 188, 190, 229-231, 251, 262, 292-\>294, 320-322, 351, 390-391, 401-402, 448 |
| src/rememberstack/adapters/converters/profile.py                                              |      193 |        4 |       66 |        3 |     97.3% |183-\>185, 375-376, 384, 386 |
| src/rememberstack/adapters/converters/spreadsheet.py                                          |      239 |       14 |       58 |        5 |     93.6% |102, 183, 285, 372-373, 505, 511-512, 514, 520-522, 542, 545 |
| src/rememberstack/adapters/converters/table.py                                                |      105 |        4 |       20 |        3 |     94.4% |66, 141, 197, 227 |
| src/rememberstack/adapters/converters/text.py                                                 |      101 |        2 |       14 |        0 |     98.3% |   194-195 |
| src/rememberstack/adapters/converters/time\_limit.py                                          |       22 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/adapters/converters/zip\_budget.py                                          |       17 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/adapters/fsit\_storage\_capture.py                                          |      285 |       54 |       78 |       25 |     73.8% |36, 43, 83, 88, 292, 338, 344, 357, 364, 367, 378, 385, 393, 399, 405-409, 414-417, 426, 437, 445, 453, 459, 469, 483, 512, 540-570, 578, 582, 586 |
| src/rememberstack/adapters/fsit\_storage\_capture\_transport.py                               |      269 |       48 |       46 |       16 |     79.7% |128, 140, 145-146, 152, 162, 192-193, 200, 203-204, 211, 233, 267-268, 271-273, 275, 315-316, 320, 326, 328-329, 336-338, 340-342, 352-353, 362, 377-378, 380, 383-384, 389-391, 402-403, 413-414, 416, 442 |
| src/rememberstack/adapters/generation\_recorder.py                                            |      134 |       17 |       14 |        4 |     85.8% |193-196, 211-231, 269-270, 288-\>290, 290-\>295, 299, 302-\>309 |
| src/rememberstack/adapters/managed/\_\_init\_\_.py                                            |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/managed/composite\_auth.py                                         |       19 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/adapters/managed/perimeter\_trust.py                                        |      205 |       16 |       72 |       12 |     89.9% |122, 145, 169, 241, 246, 303-\>305, 340-341, 387, 418-419, 421, 428-429, 442, 466, 477, 485-\>exit |
| src/rememberstack/adapters/managed/signed\_token\_auth.py                                     |      135 |        1 |       62 |        1 |     99.0% |       169 |
| src/rememberstack/adapters/openai\_decisions.py                                               |      208 |       30 |       86 |       24 |     81.0% |73, 78, 105, 109, 143-144, 149-154, 158-166, 196, 205, 207, 224-\>240, 230, 256, 268, 273, 280-\>286, 313, 316, 326, 330, 335, 340, 355, 359, 389, 394 |
| src/rememberstack/adapters/openrouter.py                                                      |      697 |       53 |      246 |       26 |     91.2% |77-78, 84, 204, 594-595, 605, 620-623, 720-732, 780, 791, 805, 826-827, 828-\>837, 833-\>837, 836, 949, 1009, 1104-1105, 1112-\>1114, 1118-1119, 1121, 1134-\>1136, 1143-1144, 1210, 1216-1220, 1313-1314, 1335-1336, 1338, 1342-\>1344, 1345-\>1354, 1358, 1383-1384, 1386, 1405, 1563-1564, 1568, 1610 |
| src/rememberstack/adapters/p1\_ranked.py                                                      |      195 |        8 |       56 |        5 |     94.8% |288, 347-349, 359-\>419, 381-382, 384, 473 |
| src/rememberstack/adapters/postgres\_p1.py                                                    |      722 |       71 |      214 |       38 |     86.6% |254, 294, 308, 340, 369, 402, 440, 480, 583, 657, 804-\>806, 944-947, 1022, 1060, 1097, 1146, 1159, 1183, 1192, 1302, 1341, 1610, 1635, 1708-1728, 1871, 1876, 1883-1887, 2007, 2099-2115, 2135, 2157, 2193-2208, 2237, 2256, 2304, 2464, 2480, 2527, 2575, 2581-2582, 2601 |
| src/rememberstack/adapters/routed.py                                                          |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/\_\_init\_\_.py                                           |       23 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/control\_plane\_spend\_lease.py                           |       65 |       18 |       22 |        8 |     70.1% |43, 45, 49-50, 75, 87, 94-97, 106, 108, 111-112, 114, 122-123, 128 |
| src/rememberstack/adapters/selfhost/forget.py                                                 |       68 |        1 |       18 |        1 |     97.7% |        23 |
| src/rememberstack/adapters/selfhost/git.py                                                    |      100 |        4 |       34 |        6 |     92.5% |55, 75-\>127, 154, 269, 313, 337-\>335 |
| src/rememberstack/adapters/selfhost/hashed\_bearer\_auth.py                                   |       47 |        8 |       12 |        4 |     79.7% |27, 46, 64-65, 70, 73-74, 76 |
| src/rememberstack/adapters/selfhost/managed\_metering.py                                      |       45 |        8 |       12 |        3 |     80.7% |42-43, 52-\>exit, 59, 66-67, 78-79, 81 |
| src/rememberstack/adapters/selfhost/minio.py                                                  |      349 |      110 |      130 |       20 |     62.0% |234, 236, 247, 250-255, 268, 282, 302, 329-395, 407-419, 425-433, 437-445, 493, 498, 503-510, 520, 540-545, 549-553, 566-567, 569-\>exit, 570, 586, 591-611, 616, 626, 651, 660-661, 671, 680-\>exit, 757 |
| src/rememberstack/adapters/selfhost/mounts.py                                                 |       28 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/object\_store.py                                          |      154 |        0 |       64 |        2 |     99.1% |117-\>120, 167-\>169 |
| src/rememberstack/adapters/selfhost/projection.py                                             |       22 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/queue.py                                                  |      122 |        8 |       30 |        3 |     92.8% |118, 189, 210-214, 226 |
| src/rememberstack/adapters/selfhost/telemetry.py                                              |       43 |        7 |        6 |        1 |     79.6% | 58, 63-68 |
| src/rememberstack/adapters/selfhost/watcher.py                                                |       31 |        1 |       10 |        1 |     95.1% |        39 |
| src/rememberstack/adapters/sentry.py                                                          |       74 |        9 |       28 |        6 |     85.3% |119-125, 152-\>160, 154-\>160, 157, 166, 169, 172 |
| src/rememberstack/adapters/systemone.py                                                       |       35 |        8 |        4 |        2 |     74.4% |44, 59, 63-68 |
| src/rememberstack/adapters/testing/\_\_init\_\_.py                                            |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/cost\_meter.py                                             |        4 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/model\_provider.py                                         |       35 |        1 |        4 |        1 |     94.9% |        50 |
| src/rememberstack/adapters/testing/profile\_refresher.py                                      |       15 |        2 |        0 |        0 |     86.7% |     25-26 |
| src/rememberstack/adapters/testing/queue.py                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/telemetry.py                                               |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/typesafe.py                                                        |       96 |        7 |       24 |        3 |     90.0% |165-166, 176, 182-184, 206 |
| src/rememberstack/adapters/vertex.py                                                          |      320 |       43 |       96 |       16 |     84.4% |210-245, 516-\>487, 569-570, 572, 580-581, 589-590, 657, 671, 680, 682, 689, 696, 699, 701, 739, 748-\>736, 750-\>752, 754-756, 784, 806 |
| src/rememberstack/client.py                                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/\_\_init\_\_.py                                                        |      100 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/assured\_operation\_linter.py                                          |       33 |        4 |       18 |        4 |     84.3% |58, 81, 88, 93 |
| src/rememberstack/core/blockizer.py                                                           |       93 |        5 |       36 |        5 |     92.2% |202, 217, 246, 252, 254 |
| src/rememberstack/core/chunker.py                                                             |       76 |        0 |       22 |        0 |    100.0% |           |
| src/rememberstack/core/concise\_adjudication.py                                               |      309 |        8 |      146 |       16 |     94.7% |74, 83-\>81, 87-\>85, 91-\>89, 132, 145, 212-\>222, 302-\>305, 342-\>347, 356, 416, 422, 425-\>430, 461-\>463, 505, 600 |
| src/rememberstack/core/consumption\_skill.py                                                  |       77 |        0 |       10 |        1 |     98.9% | 304-\>306 |
| src/rememberstack/core/content\_detection.py                                                  |      268 |       25 |      128 |       14 |     87.6% |99, 174, 208, 219-222, 231, 328, 349, 355-357, 366-\>382, 382-\>403, 399-\>403, 439-446, 451, 509, 541, 555 |
| src/rememberstack/core/context\_references.py                                                 |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/conversion.py                                                          |       85 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/core/core\_manifest.py                                                      |       21 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/corpus\_export.py                                                      |      216 |        4 |       68 |        4 |     97.2% |120, 322, 534, 652 |
| src/rememberstack/core/cursor\_scope.py                                                       |       18 |        5 |        6 |        0 |     62.5% |     40-44 |
| src/rememberstack/core/document\_filters.py                                                   |       48 |        0 |       16 |        0 |    100.0% |           |
| src/rememberstack/core/document\_metadata.py                                                  |       19 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/core/embedding\_input\_policy.py                                            |      191 |       13 |       74 |        9 |     89.4% |140-\>149, 159, 215-216, 258, 284-290, 310-\>312, 332, 333-\>335, 341 |
| src/rememberstack/core/entity\_profile\_input.py                                              |        5 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/extension\_packs.py                                                    |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/extraction\_eligibility.py                                             |       22 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/core/fact\_application.py                                                   |       44 |        6 |       28 |        6 |     83.3% |52, 62, 64, 76, 78, 82 |
| src/rememberstack/core/fact\_label.py                                                         |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/fact\_windows.py                                                       |       71 |        4 |       38 |        4 |     92.7% |55, 57, 69, 146 |
| src/rememberstack/core/file\_card.py                                                          |      150 |        3 |       36 |        2 |     97.3% |118, 254, 269 |
| src/rememberstack/core/forget.py                                                              |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/format\_registry.py                                                    |       94 |        0 |       28 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_authored.py                                                 |      170 |       25 |       72 |       15 |     83.5% |47-\>58, 116-117, 141, 160, 167, 172-173, 182, 185, 193-196, 212, 216-217, 223, 233-234, 239, 245-246, 250, 252, 255, 261-\>263 |
| src/rememberstack/core/knowledge\_compile.py                                                  |      106 |       11 |       52 |        7 |     86.1% |39, 44, 129, 131, 170-172, 184-186, 202 |
| src/rememberstack/core/knowledge\_fact\_sheet.py                                              |       77 |        5 |       30 |        5 |     90.7% |40, 82, 101, 149, 218 |
| src/rememberstack/core/knowledge\_hashing.py                                                  |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_planner.py                                                  |       33 |        4 |       14 |        4 |     83.0% |33, 35, 48, 50 |
| src/rememberstack/core/knowledge\_writer.py                                                   |       71 |        1 |       30 |        1 |     98.0% |        24 |
| src/rememberstack/core/metering.py                                                            |       29 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/core/object\_keys.py                                                        |       10 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/core/object\_transfer.py                                                    |       23 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/core/open\_query\_prose.py                                                  |       20 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/ranking.py                                                             |       66 |        8 |       20 |        6 |     83.7% |60, 125, 127, 166, 178-179, 186, 197 |
| src/rememberstack/core/section\_snap.py                                                       |       63 |        3 |       26 |        3 |     93.3% |122, 177, 203 |
| src/rememberstack/core/selection\_references.py                                               |      149 |       11 |       50 |       11 |     88.9% |117, 126, 138, 233, 235, 246, 267, 358, 361, 365, 368 |
| src/rememberstack/core/source\_origins.py                                                     |       45 |        0 |       10 |        0 |    100.0% |           |
| src/rememberstack/core/source\_passages.py                                                    |      170 |       20 |       66 |       13 |     83.5% |56, 142-144, 157, 182, 209, 266, 291, 296, 306, 308, 311, 345, 348-349, 356-358, 401 |
| src/rememberstack/core/storage\_capture.py                                                    |      114 |       17 |       54 |       16 |     80.4% |49, 94, 143, 145, 148, 153, 155, 157, 159, 165, 167, 170, 172, 174, 176, 188, 227 |
| src/rememberstack/core/storage\_routing.py                                                    |       11 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/structure\_skeleton.py                                                 |      368 |        9 |      144 |       12 |     95.9% |170, 216-\>218, 242, 245, 518, 520, 565-\>567, 644, 797-\>796, 865, 867, 930 |
| src/rememberstack/core/temporal.py                                                            |       78 |        5 |       38 |        5 |     91.4% |55, 88, 120, 143, 156 |
| src/rememberstack/core/text\_origin.py                                                        |       20 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/core/text\_scope.py                                                         |       72 |        1 |       12 |        1 |     97.6% |       120 |
| src/rememberstack/eval/\_\_init\_\_.py                                                        |       17 |        2 |        2 |        1 |     84.2% |  147, 156 |
| src/rememberstack/eval/consumption.py                                                         |       43 |        2 |        8 |        2 |     92.2% |    82, 85 |
| src/rememberstack/eval/contradiction.py                                                       |       45 |        1 |       10 |        1 |     96.4% |       111 |
| src/rememberstack/eval/harness.py                                                             |       41 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/eval/lifecycle.py                                                           |       57 |        2 |        8 |        2 |     93.8% |   71, 178 |
| src/rememberstack/eval/operational\_scale.py                                                  |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/eval/resolution.py                                                          |       76 |        1 |       16 |        1 |     97.8% |       311 |
| src/rememberstack/eval/retrieval\_spikes.py                                                   |       16 |       16 |        0 |        0 |      0.0% |      3-41 |
| src/rememberstack/eval/skeleton.py                                                            |       73 |        7 |       26 |        7 |     85.9% |100, 109, 128, 151, 186, 189, 220 |
| src/rememberstack/llm/\_\_init\_\_.py                                                         |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/\_\_init\_\_.py                                                       |      392 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/adjudication.py                                                       |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/assured\_operations.py                                                |       70 |        1 |        6 |        1 |     97.4% |       133 |
| src/rememberstack/model/auth.py                                                               |       42 |        0 |        8 |        0 |    100.0% |           |
| src/rememberstack/model/blocks.py                                                             |       28 |        2 |        4 |        2 |     87.5% |    41, 47 |
| src/rememberstack/model/chunks.py                                                             |      109 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/claims.py                                                             |      142 |        1 |       12 |        1 |     98.7% |       179 |
| src/rememberstack/model/client.py                                                             |       55 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/clustering.py                                                         |       22 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/component\_version.py                                                 |       65 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/model/concise\_adjudication.py                                              |       61 |        6 |       16 |        6 |     84.4% |52, 114, 126, 131, 139, 141 |
| src/rememberstack/model/consumption.py                                                        |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/content\_detection.py                                                 |        4 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/conversion.py                                                         |      171 |       11 |       22 |        2 |     90.2% |111, 206, 299-301, 328-330, 348-350 |
| src/rememberstack/model/deployment.py                                                         |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/document\_metadata.py                                                 |       26 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/documents.py                                                          |      160 |        3 |        6 |        2 |     97.0% |145, 147, 301 |
| src/rememberstack/model/envelope.py                                                           |      227 |        2 |        8 |        2 |     98.3% |  109, 706 |
| src/rememberstack/model/evaluation.py                                                         |       27 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/fact\_application.py                                                  |       61 |        6 |       16 |        6 |     84.4% |32, 97, 109, 116, 124, 126 |
| src/rememberstack/model/fact\_windows.py                                                      |       60 |        4 |       28 |        4 |     90.9% |80, 85, 90, 119 |
| src/rememberstack/model/forget.py                                                             |       62 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/git.py                                                                |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/knowledge.py                                                          |      369 |       24 |       38 |       14 |     88.7% |216, 255, 339, 351, 370, 382, 412, 571, 588, 616-627, 639-641, 670, 685, 706, 744, 753 |
| src/rememberstack/model/knowledge\_authored.py                                                |      135 |        4 |        8 |        3 |     95.1% |29, 101, 112, 192 |
| src/rememberstack/model/knowledge\_planner.py                                                 |      212 |       15 |       32 |       11 |     88.5% |47, 106, 139, 145, 160, 189, 194, 196, 238, 275-277, 291, 312, 379 |
| src/rememberstack/model/lifecycle.py                                                          |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/metering.py                                                           |       94 |        2 |       10 |        2 |     96.2% |  102, 154 |
| src/rememberstack/model/model\_provider.py                                                    |       45 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/mounts.py                                                             |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/object\_store.py                                                      |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/occurrence\_provenance.py                                             |      179 |       11 |       48 |        8 |     91.6% |150-151, 165, 168-\>174, 195, 270, 289-290, 340, 385, 403, 421 |
| src/rememberstack/model/operational\_scale.py                                                 |       25 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/operations.py                                                         |       50 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/processing.py                                                         |       94 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/processing\_layers.py                                                 |       65 |        0 |       16 |        0 |    100.0% |           |
| src/rememberstack/model/queue.py                                                              |       48 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/relations.py                                                          |       44 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/model/resolution.py                                                         |       46 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/retrieval\_spikes.py                                                  |       26 |        7 |        2 |        0 |     67.9% | 49-56, 61 |
| src/rememberstack/model/sections.py                                                           |      190 |        3 |       10 |        3 |     97.0% |293, 297, 303 |
| src/rememberstack/model/spend\_lease.py                                                       |       42 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/storage\_capture.py                                                   |      105 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/telemetry.py                                                          |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/\_\_init\_\_.py                                                       |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/auth.py                                                               |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/connector.py                                                          |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/cost\_meter.py                                                        |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/forget.py                                                             |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/git.py                                                                |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/metering.py                                                           |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/model\_provider.py                                                    |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/mounts.py                                                             |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/object\_store.py                                                      |       24 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/p1\_index.py                                                          |      118 |        2 |       18 |        2 |     97.1% |  321, 328 |
| src/rememberstack/ports/postgres\_read.py                                                     |       11 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/profile\_refresher.py                                                 |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/purge.py                                                              |       26 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/queue.py                                                              |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/storage\_capture.py                                                   |       18 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/systemone.py                                                          |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/telemetry.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/profiles/\_\_init\_\_.py                                                    |        7 |        1 |        2 |        1 |     77.8% |        20 |
| src/rememberstack/profiles/managed\_storage\_capture.py                                       |      428 |      267 |      162 |        1 |     29.2% |136-153, 184, 234-242, 251-322, 326-338, 346-360, 371-413, 421-432, 447-450, 459-525, 545-593, 614-638, 644-667, 685-796, 800-815, 819-827, 842-852, 867-931, 942-959, 967-982, 996-1020, 1045, 1052-1056, 1061 |
| src/rememberstack/profiles/selfhost.py                                                        |      875 |      155 |      174 |       23 |     80.9% |305, 340, 345, 349, 353, 408, 433, 562-563, 660, 677, 706, 716, 724, 732-748, 826-856, 869-879, 921, 1077-1095, 1102-1231, 1276, 1281-1283, 1458-1463, 1471-1478, 1495-1508, 1554, 1559, 1619-1622, 1682, 1918, 1936-1940, 1968-2005, 2013, 2017, 2032, 2040, 2135, 2278 |
| src/rememberstack/profiles/selfhost\_forget.py                                                |       90 |       18 |        4 |        0 |     76.6% |79-83, 170, 181, 204, 215, 232-243, 248-249, 256, 263 |
| src/rememberstack/profiles/selfhost\_operations.py                                            |       36 |        4 |        0 |        0 |     88.9% | 43, 68-70 |
| src/rememberstack/profiles/storage\_capture\_batch.py                                         |      119 |       66 |       26 |        0 |     36.6% |64-93, 112-113, 156, 177-261 |
| src/rememberstack/profiles/storage\_capture\_profile.py                                       |       97 |        4 |       12 |        4 |     92.7% |267, 279, 336, 394 |
| src/rememberstack/spine/\_\_init\_\_.py                                                       |       57 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/admission.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/apply\_fact\_decision.py                                              |      137 |       12 |       36 |        3 |     89.0% |50, 78, 231-256 |
| src/rememberstack/spine/assured\_operations.py                                                |       88 |        3 |        8 |        1 |     95.8% | 40-41, 93 |
| src/rememberstack/spine/backfill.py                                                           |       28 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/catalog\_contract.py                                                  |      161 |       24 |       74 |       24 |     79.6% |524, 549, 562, 617, 636, 664, 677, 718, 731, 746, 768, 792, 809, 820, 840, 896, 936, 938, 940, 942, 944, 946, 948, 1000 |
| src/rememberstack/spine/chunk\_catalog.py                                                     |       84 |        3 |       14 |        3 |     93.9% |41, 98, 255 |
| src/rememberstack/spine/claim\_catalog.py                                                     |      140 |       12 |       48 |        8 |     87.2% |158-160, 176, 196, 247, 255, 317, 334-336, 591 |
| src/rememberstack/spine/clustering.py                                                         |      290 |       14 |      108 |       17 |     92.2% |67, 142, 202-\>180, 248, 318, 387, 468-\>447, 534-\>523, 615, 617, 628, 633, 676, 679, 698, 799, 804 |
| src/rememberstack/spine/component\_versions.py                                                |       56 |        3 |       12 |        3 |     91.2% |102, 119, 187 |
| src/rememberstack/spine/consumption.py                                                        |       20 |        1 |        2 |        1 |     90.9% |        32 |
| src/rememberstack/spine/corpus\_export.py                                                     |       23 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/corpus\_publication.py                                                |      139 |       15 |       34 |       11 |     85.0% |84, 126, 153-154, 158, 164, 172, 284-\>295, 292-293, 295-\>exit, 303-304, 347, 384-390, 412, 439-\>414 |
| src/rememberstack/spine/cost\_export.py                                                       |      212 |       15 |       40 |        8 |     90.9% |96, 192, 283, 302-303, 336, 344-345, 359, 377, 387-388, 390, 397, 430 |
| src/rememberstack/spine/deployment\_bootstrap.py                                              |       84 |        1 |       20 |        1 |     98.1% |       245 |
| src/rememberstack/spine/document\_bindings.py                                                 |      105 |        7 |       34 |        7 |     89.9% |91-\>109, 112-\>114, 118, 152, 162, 169, 174-175, 220 |
| src/rememberstack/spine/document\_catalog.py                                                  |      238 |        4 |       40 |        5 |     96.8% |458, 543, 572, 789, 800-\>840 |
| src/rememberstack/spine/document\_inventory.py                                                |       51 |        1 |        6 |        1 |     96.5% |       238 |
| src/rememberstack/spine/document\_metadata.py                                                 |       72 |        3 |       18 |        3 |     93.3% |112, 152, 274 |
| src/rememberstack/spine/document\_references.py                                               |      487 |       24 |      166 |       25 |     92.2% |112, 197, 209, 219, 396, 400-\>394, 451, 453, 455, 471-\>473, 489, 503, 578, 609, 688, 704, 767-\>775, 882, 910-915, 955, 1079, 1131, 1132-\>1136, 1135, 1140 |
| src/rememberstack/spine/document\_search.py                                                   |      324 |        3 |       92 |        4 |     98.3% |567, 571-\>562, 796, 1270 |
| src/rememberstack/spine/effective\_time.py                                                    |      128 |        1 |       38 |        1 |     98.8% |        95 |
| src/rememberstack/spine/entity\_eligibility.py                                                |       29 |        3 |       12 |        3 |     85.4% |48, 60, 89 |
| src/rememberstack/spine/entity\_registry.py                                                   |       49 |        6 |        4 |        1 |     83.0% |64-\>84, 121, 125-131 |
| src/rememberstack/spine/extension\_packs.py                                                   |       40 |        1 |       16 |        1 |     96.4% |       119 |
| src/rememberstack/spine/fact\_adjudication.py                                                 |      312 |       20 |      112 |       24 |     89.6% |331, 338-\>303, 424-425, 428, 430, 490, 505-\>512, 526, 530, 534, 587-\>592, 588-\>587, 598-\>652, 610-\>614, 611-\>610, 624, 628-\>652, 666-667, 698, 798, 813, 825, 827, 882, 932-933, 1016-\>1035 |
| src/rememberstack/spine/fact\_application\_inputs.py                                          |      126 |        7 |       40 |        8 |     91.0% |66, 94, 187, 264, 293, 442, 533, 537-\>534 |
| src/rememberstack/spine/fact\_applications.py                                                 |      119 |        6 |       30 |        6 |     91.9% |54, 96, 196, 204, 263, 536 |
| src/rememberstack/spine/fact\_catalog.py                                                      |      155 |       27 |       22 |        4 |     80.2% |49, 64, 90-91, 104-\>exit, 124, 310-\>312, 335, 346-355, 361-362, 380-381, 405-424, 430-442, 473-484 |
| src/rememberstack/spine/fact\_graph\_contract.py                                              |       36 |        0 |       10 |        0 |    100.0% |           |
| src/rememberstack/spine/fact\_window\_review.py                                               |      174 |        8 |       40 |        7 |     93.0% |193, 209, 269, 304, 394-403, 500, 575 |
| src/rememberstack/spine/forget.py                                                             |      231 |       45 |       52 |       16 |     74.2% |51, 65-75, 87-92, 101, 182, 200-213, 217-221, 228, 258, 262, 272, 365-366, 389, 410, 436-449, 482, 494-511, 534, 551, 733 |
| src/rememberstack/spine/graph\_catalog.py                                                     |       92 |       13 |       34 |       11 |     81.0% |95-98, 110, 148, 152-\>154, 156, 197, 280-281, 284, 301-302, 314, 393 |
| src/rememberstack/spine/knowledge.py                                                          |     1266 |      118 |      464 |       99 |     86.8% |153, 168, 239, 249, 277, 283-\>exit, 296, 307, 319, 323, 377, 419, 453, 514-519, 557, 625-634, 649, 668, 696-\>692, 763, 777, 888, 904, 939, 969, 1060, 1094, 1196, 1247, 1283, 1292, 1347, 1367, 1398, 1451-1454, 1481, 1486, 1488, 1490, 1546, 1548, 1550, 1552, 1554, 1571, 1578, 1582, 1613, 1624, 1626-\>1644, 1683, 1799, 1828, 1846, 1871-1877, 1983-1986, 2095, 2119, 2142, 2183, 2190, 2192-2193, 2200, 2213, 2238-2251, 2312, 2346, 2364, 2374, 2401, 2403, 2407, 2416, 2441, 2469, 2598, 2609, 2613, 2630, 2633, 2705, 2736, 2753, 2781, 2805, 2811-\>2822, 2822-\>2833, 2858, 2874, 2951-\>2956, 2980-2984, 3099, 3103-\>3116, 3116-\>3128, 3128-\>3135, 3171, 3194-3200, 3241-\>3251, 3251-\>3264, 3379-3383, 3444-3453, 3563, 3582 |
| src/rememberstack/spine/lifecycle.py                                                          |      292 |        6 |       32 |        3 |     97.2% |165, 259-268, 568, 600 |
| src/rememberstack/spine/managed\_metering.py                                                  |      207 |       21 |       36 |        6 |     88.9% |129, 164-177, 194, 196, 281-289, 364-\>277, 423-438, 443-\>419 |
| src/rememberstack/spine/migrations/\_\_init\_\_.py                                            |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/\_helpers.py                                               |      151 |        8 |       88 |        4 |     94.1% |163-168, 189-191, 198-\>202, 204-\>206 |
| src/rememberstack/spine/migrations/env.py                                                     |       50 |        6 |       10 |        4 |     83.3% |15-\>18, 35, 40-48, 81, 109 |
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
| src/rememberstack/spine/migrations/versions/p9\_02\_0023\_query\_space\_roles.py              |       57 |        0 |       12 |        0 |    100.0% |           |
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
| src/rememberstack/spine/migrations/versions/p9\_17\_0038\_postgres19\_live\_graph.py          |       68 |        0 |        8 |        0 |    100.0% |           |
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
| src/rememberstack/spine/migrations/versions/p9\_35\_0056\_document\_metadata.py               |       24 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_36\_0057\_own\_document\_name\_span.py        |       16 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_37\_0058\_extraction\_eligibility.py          |       25 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_38\_0059\_d140\_effective\_time.py            |      190 |        4 |       52 |        4 |     96.7% |1180, 1210, 1360, 1749 |
| src/rememberstack/spine/migrations/versions/p9\_39\_0060\_d140\_period\_window\_review.py     |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_40\_0061\_citation\_path\_projection.py       |       30 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_41\_0062\_processing\_layers.py               |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_42\_0063\_entity\_counts\_demand.py           |       11 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_43\_0064\_claim\_leases.py                    |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_44\_0065\_drop\_property\_graphs.py           |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_45\_0066\_fact\_counts\_demand.py             |       21 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_46\_0067\_generic\_meter.py                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_47\_0068\_mention\_chunk\_lookup.py           |      114 |       14 |       56 |       15 |     82.9% |119, 125, 149, 156, 165, 208, 210, 234, 260, 262, 284, 287, 288-\>279, 294, 300 |
| src/rememberstack/spine/migrations/versions/p9\_48\_0069\_shared\_eligibility.py              |       43 |        0 |        8 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_49\_0070\_corpus\_publication\_marker.py      |       11 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_50\_0071\_corpus\_publication\_work.py        |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_51\_0072\_corpus\_paths.py                    |       11 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_52\_0073\_corpus\_reconcile.py                |       11 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/migrations/versions/p9\_53\_0074\_document\_tags.py                   |       33 |        1 |       10 |        1 |     95.3% |       136 |
| src/rememberstack/spine/new\_work.py                                                          |       36 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/spine/observation\_adjudication.py                                          |       98 |       27 |       18 |        5 |     65.5% |132, 148, 161, 201, 209-221, 251, 256, 261, 271, 281-296, 301-304, 319 |
| src/rememberstack/spine/operations.py                                                         |       81 |        1 |        4 |        1 |     97.6% |       161 |
| src/rememberstack/spine/perimeter\_state.py                                                   |       24 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/period\_adjudication.py                                               |      119 |        3 |       28 |        3 |     95.9% |197, 582, 682 |
| src/rememberstack/spine/postgres\_graph\_sql.py                                               |       17 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/processing\_layers.py                                                 |      359 |       14 |      120 |        8 |     95.0% |165-172, 407-408, 510-516, 635, 730, 731-\>738, 969, 1030, 1166, 1180 |
| src/rememberstack/spine/profile\_convergence.py                                               |       24 |        6 |        0 |        0 |     75.0% |30-37, 48-55 |
| src/rememberstack/spine/profile\_refresher.py                                                 |      247 |       13 |       82 |       13 |     92.1% |112, 124-\>100, 126, 191, 202, 222, 279, 302-303, 354, 396, 487, 579, 671, 707-\>681 |
| src/rememberstack/spine/query\_space/\_\_init\_\_.py                                          |       40 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/ast\_serializer.py                                       |       27 |        4 |       10 |        2 |     83.8% |77-78, 82, 102 |
| src/rememberstack/spine/query\_space/canonical.py                                             |       71 |        2 |       32 |        1 |     97.1% |   89, 137 |
| src/rememberstack/spine/query\_space/catalog.py                                               |       31 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/deletion\_matrix.py                                      |       80 |        2 |       18 |        1 |     96.9% |  574, 580 |
| src/rememberstack/spine/query\_space/manifest.py                                              |      192 |       18 |       48 |       13 |     87.1% |159, 657, 673, 719, 808, 814, 822, 892, 972, 989, 996-999, 1004, 1009, 1020, 1031, 1171, 1173 |
| src/rememberstack/spine/query\_space/quarantine.py                                            |       20 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/source\_definitions.py                                   |      121 |        4 |       32 |        4 |     94.8% |194, 258, 261, 283 |
| src/rememberstack/spine/rank\_embed\_cache.py                                                 |      105 |       11 |       30 |        9 |     85.2% |44, 46, 48, 50, 89, 101, 125-127, 148-\>152, 175, 181 |
| src/rememberstack/spine/readiness.py                                                          |      213 |       25 |       76 |       10 |     85.1% |85, 558-566, 582, 625, 627-\>641, 634-\>627, 642, 666, 668, 676, 678, 682-685, 688-691 |
| src/rememberstack/spine/references.py                                                         |      233 |        6 |       44 |        4 |     96.4% |358-359, 368, 506, 535, 743 |
| src/rememberstack/spine/resolver.py                                                           |      357 |       15 |       96 |       11 |     94.3% |523, 552, 869-872, 880-887, 897-902, 942, 1018, 1059, 1228, 1240, 1245, 1303 |
| src/rememberstack/spine/retrieval\_eligibility.py                                             |       22 |        1 |        4 |        1 |     92.3% |       198 |
| src/rememberstack/spine/review.py                                                             |      197 |       14 |       58 |       14 |     89.0% |140, 249, 304, 345, 385-\>396, 482-486, 533, 535, 603, 607, 626, 641, 670, 927 |
| src/rememberstack/spine/section\_history.py                                                   |      207 |        4 |       54 |        4 |     96.9% |299, 428, 489, 642 |
| src/rememberstack/spine/section\_index\_backfill.py                                           |       73 |        7 |       10 |        3 |     88.0% |91, 110-115, 132-137, 156 |
| src/rememberstack/spine/selection\_catalog.py                                                 |       59 |        3 |       10 |        3 |     91.3% |211, 220, 223 |
| src/rememberstack/spine/settings.py                                                           |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/storage\_capture\_catalog.py                                          |       79 |        3 |        6 |        3 |     92.9% |298, 310, 345 |
| src/rememberstack/spine/storage\_capture\_guard.py                                            |       75 |        7 |       14 |        7 |     84.3% |294, 296, 305, 307, 324, 331, 380 |
| src/rememberstack/spine/storage\_capture\_view.py                                             |      224 |      150 |       64 |        0 |     25.7% |107-116, 125-140, 144-166, 172-187, 191-192, 196-199, 203-215, 221-222, 235-236, 249-393, 398-409, 416-427, 432-442, 462-470, 477-481, 492-504 |
| src/rememberstack/spine/supersession.py                                                       |       13 |        1 |        0 |        0 |     92.3% |        36 |
| src/rememberstack/spine/surface\_cost.py                                                      |      136 |       23 |       12 |        4 |     80.4% |100-101, 138, 162-164, 167-169, 196-198, 202-215, 259-260 |
| src/rememberstack/spine/sync.py                                                               |       32 |        0 |        2 |        1 |     97.1% |   90-\>98 |
| src/rememberstack/spine/tags.py                                                               |      118 |        1 |       20 |        1 |     98.6% |       300 |
| src/rememberstack/spine/work\_ledger.py                                                       |      543 |       43 |      180 |       38 |     88.2% |91, 171, 291, 293, 317, 347, 349, 369, 434, 436, 454, 517, 519, 568, 590, 646, 648, 658, 683, 685, 750-757, 785, 827, 945, 971, 990, 1039, 1050, 1068, 1116, 1300, 1312-\>1327, 1383, 1528, 1539, 1549, 1718, 1789, 1912-\>1927 |
| src/rememberstack/surfaces/\_\_init\_\_.py                                                    |       14 |        2 |        0 |        0 |     85.7% |   101-102 |
| src/rememberstack/surfaces/consumption\_skill.py                                              |       42 |        3 |        8 |        2 |     90.0% |35, 67, 86 |
| src/rememberstack/surfaces/cost\_export\_api.py                                               |      112 |       37 |       24 |        1 |     63.2% |121-125, 146-162, 167-192, 201 |
| src/rememberstack/surfaces/direct\_admission.py                                               |      134 |        1 |       46 |        2 |     98.3% |167, 174-\>exit |
| src/rememberstack/surfaces/graph\_queries.py                                                  |      403 |       36 |      132 |       33 |     86.7% |88, 106, 108, 110, 139, 161, 301, 360, 371, 425, 427, 474-475, 488, 491, 500, 534-\>537, 731, 737, 771-776, 829, 833, 842, 892, 919, 957, 990, 1007, 1109, 1111, 1177, 1191, 1202, 1216, 1218 |
| src/rememberstack/surfaces/http\_api.py                                                       |     1052 |       50 |      250 |       12 |     94.6% |489, 821, 870, 1282, 1526-1532, 1584-1590, 1597-1625, 1682, 1791, 1880-1881, 1997, 2088, 2171-2172, 2221, 2270-\>2274, 2317, 2323-2324, 2512, 2514, 2619-2620, 2650-2651, 2725, 2980, 3072-\>3075, 3090-3091, 3116, 3137-3141, 3149-3150, 3156-3157 |
| src/rememberstack/surfaces/mcp.py                                                             |      279 |       17 |       54 |        6 |     93.1% |216-217, 241-242, 478, 493-494, 509-510, 591, 601-604, 615-618, 634-639, 670 |
| src/rememberstack/surfaces/operation\_executor.py                                             |       55 |        1 |        8 |        1 |     96.8% |       187 |
| src/rememberstack/surfaces/operation\_surface.py                                              |      139 |       18 |       64 |       10 |     83.3% |206, 218, 220, 227-229, 235-239, 248, 257-258, 275, 281, 284, 286 |
| src/rememberstack/surfaces/query\_engine.py                                                   |     1609 |      150 |      478 |       79 |     87.6% |280, 285-292, 294, 350-351, 580, 592-601, 672-677, 764, 808, 869, 875-\>871, 947, 963-966, 1016, 1020, 1215, 1309-1311, 1353, 1356, 1358, 1372, 1425, 1688, 1696-1697, 1718-1726, 1735, 1762-1763, 1798, 1809, 1828-1829, 1853, 1980, 1996-2001, 2100, 2203, 2212, 2260, 2287, 2396-2399, 2445-2448, 2512, 2622-2625, 2721, 2785, 2837, 2859-2861, 2898, 2918-\>2920, 2921, 2960, 2981, 3020, 3067, 3153, 3229-\>3231, 3232, 3574-3603, 3660-3690, 3717, 3758, 3836, 3935-3952, 3957-\>3971, 4021-4031, 4098, 4100, 4172, 4210, 4269, 4301, 4346, 4365-4366, 4404, 4413, 4436-4444, 4479-\>4481, 4487, 4489, 4497, 4511, 4541, 4583, 4596, 4635, 4637, 4655, 4695, 4801, 4881, 4954-\>4971, 5068, 5072, 5400, 5493, 6280 |
| src/rememberstack/surfaces/query\_sandbox/\_\_init\_\_.py                                     |       21 |       14 |        6 |        0 |     25.9% |     50-66 |
| src/rememberstack/surfaces/query\_sandbox/audit.py                                            |       92 |        7 |       14 |        4 |     89.6% |109-\>116, 153-154, 157-158, 178, 203, 207 |
| src/rememberstack/surfaces/query\_sandbox/bridge.py                                           |      302 |       26 |       98 |       15 |     88.8% |223, 231-232, 239, 285, 301, 315, 366, 442, 475, 485, 578, 675, 691, 716, 728-729, 822, 848, 931, 942-947 |
| src/rememberstack/surfaces/query\_sandbox/discovery.py                                        |      106 |        3 |       24 |        3 |     95.4% |117, 179, 227 |
| src/rememberstack/surfaces/query\_sandbox/errors.py                                           |        5 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/examples.py                                         |       67 |        1 |        6 |        1 |     97.3% |       473 |
| src/rememberstack/surfaces/query\_sandbox/executor.py                                         |      370 |       31 |      114 |       21 |     88.8% |123, 136, 139-140, 145, 153, 187-188, 193, 199, 206, 220-225, 334, 340, 351, 353, 355, 358-360, 375, 536, 556, 580-585, 728, 896-899, 964-965 |
| src/rememberstack/surfaces/query\_sandbox/grammar.py                                          |      658 |       53 |      254 |       33 |     89.3% |248, 252, 257, 391, 416, 464, 568-\>567, 592, 609-610, 613-622, 645, 728, 734, 786, 790-791, 798, 808, 906, 912-921, 935-938, 944-\>941, 988-\>986, 1059-\>1061, 1079, 1103-\>1107, 1123-1124, 1127, 1153-\>1151, 1160, 1208, 1255-\>1261, 1291, 1297-1303, 1309-1310, 1314-1317, 1366, 1420 |
| src/rememberstack/surfaces/query\_sandbox/layers.py                                           |       38 |        3 |       14 |        3 |     88.5% |101, 105-\>110, 108-109, 110-\>99 |
| src/rememberstack/surfaces/query\_sandbox/limits.py                                           |       23 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/nomination.py                                       |      284 |       30 |       98 |       16 |     87.4% |204-205, 210, 279-287, 294, 498-499, 563, 565, 567, 681-687, 693, 710-711, 748, 770-771, 779, 811-812, 835-\>837, 858, 885 |
| src/rememberstack/surfaces/query\_sandbox/open\_query.py                                      |       63 |        3 |       10 |        2 |     93.2% |75, 150, 210 |
| src/rememberstack/surfaces/query\_sandbox/result.py                                           |       70 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/saved\_queries.py                                   |      585 |       64 |      240 |       56 |     84.7% |205, 223, 286, 447, 497, 503, 508, 545, 590, 601, 609, 649, 737, 803, 809, 825, 875-\>885, 943, 964, 1048, 1078, 1101, 1274, 1360, 1547-\>1549, 1662, 1667, 1751, 1919, 1925, 1945, 1958, 1963, 1972-1983, 1990, 2000, 2006, 2016, 2023, 2040, 2046-2051, 2057, 2078, 2080, 2083, 2103, 2153, 2158, 2175-2180, 2183, 2187, 2191, 2201, 2236, 2253-\>2257, 2268, 2296-2298 |
| src/rememberstack/surfaces/route\_scope.py                                                    |       25 |        0 |       14 |        0 |    100.0% |           |
| src/rememberstack/workers/\_\_init\_\_.py                                                     |       78 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/base.py                                                             |      234 |       17 |       46 |        3 |     92.9% |257-263, 265-271, 293, 410-411, 434-435, 464-465, 499-500, 573, 671-672 |
| src/rememberstack/workers/corpus\_export.py                                                   |      108 |        0 |       42 |        1 |     99.3% | 192-\>194 |
| src/rememberstack/workers/corpus\_publication.py                                              |      235 |       17 |       92 |       16 |     89.3% |78, 102, 144-145, 179, 189-\>185, 217, 227, 228-\>224, 237, 252, 298, 304, 340, 343-\>exit, 397, 399-400, 406-407, 429-\>433 |
| src/rememberstack/workers/e0.py                                                               |      742 |       52 |      180 |       25 |     91.0% |268, 384, 410, 453-454, 489, 756, 777-782, 837-\>846, 848-857, 1031, 1055-1064, 1139-1140, 1499, 1699-1716, 1776, 1860-\>1859, 1866, 1869, 1980, 2024-2025, 2066, 2131, 2213, 2248, 2309, 2327, 2342, 2363-2365, 2374, 2408-\>2413, 2414, 2421, 2429 |
| src/rememberstack/workers/e0\_crossref.py                                                     |       21 |        1 |        2 |        1 |     91.3% |        37 |
| src/rememberstack/workers/e0\_summary.py                                                      |      392 |       43 |      118 |       22 |     85.3% |351-352, 354-358, 425, 482, 503, 521, 530, 552, 554-556, 570-571, 576, 587, 609, 628, 636, 651, 662, 666-682, 694-697, 707, 717, 906, 917, 928, 945, 957-967 |
| src/rememberstack/workers/e1.py                                                               |      263 |       31 |       80 |       14 |     83.4% |303, 329, 335-\>330, 355-\>339, 399, 413, 433, 459, 470-501, 532, 534, 569-\>571, 571-\>exit, 585-607, 634-637, 885 |
| src/rememberstack/workers/e2.py                                                               |      611 |       37 |      218 |       34 |     91.2% |489, 501, 642, 682, 711, 729, 848, 1079, 1122-\>1129, 1125, 1133-1134, 1153, 1166, 1175, 1184, 1287, 1423-\>1425, 1503-\>1502, 1516-\>1520, 1538, 1580, 1700-1701, 1702-\>1734, 1704-\>1734, 1708, 1713, 1727, 1729-\>1734, 1862, 1881, 1957, 2010, 2024, 2037, 2070-2073, 2112-2114, 2205, 2250 |
| src/rememberstack/workers/e3.py                                                               |      272 |       19 |       78 |       13 |     90.3% |229, 244, 258, 286, 347, 404, 442, 484-499, 574, 609, 620, 624, 711, 726-\>745 |
| src/rememberstack/workers/extraction\_references.py                                           |       96 |        7 |       30 |        6 |     89.7% |72-73, 97, 207, 242, 258, 260 |
| src/rememberstack/workers/fact\_window\_review.py                                             |       55 |        3 |       10 |        3 |     90.8% |65, 72, 178 |
| src/rememberstack/workers/forget.py                                                           |      125 |       18 |       34 |        4 |     83.6% |103-108, 163, 174-179, 191, 289, 298-306 |
| src/rememberstack/workers/knowledge\_authored.py                                              |       77 |        5 |       16 |        3 |     91.4% |55, 109, 117, 128-129 |
| src/rememberstack/workers/knowledge\_driver.py                                                |      295 |       53 |       88 |       14 |     77.3% |166, 247-258, 290, 495-511, 515, 562-\>564, 591-611, 625, 629, 633, 641-647, 654-672, 696, 699-700, 702, 705-706, 708, 734 |
| src/rememberstack/workers/knowledge\_fact\_sheet.py                                           |       41 |        2 |        2 |        1 |     93.0% |    31, 57 |
| src/rememberstack/workers/knowledge\_planner.py                                               |      136 |       13 |       20 |        7 |     87.2% |74, 108, 176, 199, 207, 209, 214, 243-251, 274-275 |
| src/rememberstack/workers/knowledge\_writer.py                                                |      157 |       12 |       22 |       11 |     87.2% |81, 101, 147, 181, 198, 203, 205, 308, 345, 351, 353, 374 |
| src/rememberstack/workers/lane\_checkpoints.py                                                |       25 |        2 |        0 |        0 |     92.0% |     48-49 |
| src/rememberstack/workers/operations.py                                                       |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/p1.py                                                               |      125 |        4 |       30 |        5 |     94.2% |177, 184, 192, 253-\>265, 384 |
| src/rememberstack/workers/reconcile.py                                                        |      263 |       14 |       64 |       13 |     91.7% |164, 245-246, 253, 311, 365-366, 375, 412, 417, 451-\>443, 453, 495-\>499, 641-\>652, 678-\>682, 740, 989, 1012 |
| src/rememberstack/workers/section\_orientation.py                                             |       48 |        4 |       18 |        4 |     87.9% |46, 83, 95, 97 |
| src/rememberstack/workers/sync.py                                                             |       70 |        0 |       18 |        1 |     98.9% |  108-\>85 |
| **TOTAL**                                                                                     | **46210** | **3911** | **11822** | **1839** | **88.8%** |           |


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