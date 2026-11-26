# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/writeitai/remember-stack/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                                          |    Stmts |     Miss |   Branch |   BrPart |     Cover |   Missing |
|---------------------------------------------------------------------------------------------- | -------: | -------: | -------: | -------: | --------: | --------: |
| src/remember/\_\_init\_\_.py                                                                  |       89 |        5 |        0 |        0 |     94.4% |     93-97 |
| src/remember/\_\_main\_\_.py                                                                  |        5 |        5 |        2 |        0 |      0.0% |      3-10 |
| src/remember/cli.py                                                                           |     1154 |       94 |      306 |       39 |     89.8% |137, 217-229, 248, 267-\>269, 275, 291, 295-299, 364-\>exit, 367-\>371, 376-383, 393-395, 401-408, 432, 442-\>444, 468, 474, 499-501, 509, 512, 519, 547, 596, 605, 616, 645, 669-670, 678-680, 685-701, 712, 726-727, 744-745, 764-765, 771-772, 780, 952, 956-957, 995-\>999, 1090-1091, 1100, 1125, 1239, 1303, 1341, 1357, 1376-1379, 1437, 1457, 1501-\>1506, 1528, 1539-1541, 1556-1558, 1590-1598, 1683, 2722-2726 |
| src/remember/cli\_output.py                                                                   |       32 |        0 |        2 |        0 |    100.0% |           |
| src/remember/client.py                                                                        |      836 |       70 |      304 |       49 |     89.2% |237, 298-299, 305-306, 334, 336, 338, 340, 369, 400, 411-413, 445, 466, 488, 509, 535, 588, 619-\>621, 622, 624, 628, 659, 667-669, 902, 959, 969, 983, 1045, 1088, 1096, 1099, 1126, 1591, 1649, 1685-\>1689, 1764-\>1766, 1821-1822, 1859, 1866, 1868, 1871, 1898, 1920, 1963, 2091, 2107, 2109, 2111-\>2113, 2133, 2151, 2183-2184, 2188, 2193-2194, 2215-2216, 2285-2286, 2289-\>2291, 2292-2293, 2306-2307, 2313, 2319, 2346, 2349-2351 |
| src/remember/connection.py                                                                    |      192 |       10 |       46 |        5 |     93.7% |108, 110, 283-284, 346, 365-366, 368-\>370, 405, 408-409 |
| src/remember/credentials.py                                                                   |      207 |       35 |       46 |       13 |     77.9% |67-69, 110, 118, 120, 161-164, 165-\>174, 168-169, 181, 234, 236, 249-\>exit, 258, 275-277, 283-286, 293, 308, 318-320, 340-348, 385-386 |
| src/remember/errors.py                                                                        |       59 |        0 |        8 |        0 |    100.0% |           |
| src/remember/http\_routes.py                                                                  |        5 |        0 |        0 |        0 |    100.0% |           |
| src/remember/issuer.py                                                                        |      261 |       33 |       56 |       12 |     85.8% |117-\>124, 122, 126, 146, 157-158, 179-180, 204, 215, 296, 306, 384-385, 389, 394-395, 425, 435-438, 447, 451, 465-466, 470, 475, 488-489, 524-527 |
| src/remember/login.py                                                                         |      215 |       23 |       62 |        8 |     88.8% |75-80, 144-\>148, 227-228, 233-234, 253-254, 276, 283, 294-295, 318, 324, 348-\>354, 350, 366-368, 370, 377-379 |
| src/remember/mcp\_bridge.py                                                                   |      241 |       24 |       94 |       18 |     87.5% |99, 102-106, 108-115, 140, 196, 199-\>201, 202-\>204, 254, 297, 312, 316, 321-\>exit, 338-339, 349, 373-\>375, 379, 382-\>exit, 389-390, 394-\>393, 403, 420, 423, 428, 435-436 |
| src/remember/mcp\_engine.py                                                                   |      192 |       10 |       52 |        8 |     92.6% |314, 357, 363, 365, 390, 395, 400, 426, 429-430 |
| src/remember/mcp\_http.py                                                                     |      210 |       31 |       46 |        7 |     85.2% |83, 105, 139, 171-172, 177-179, 235-236, 242-243, 251-254, 256-264, 314-315, 365-375, 386-398 |
| src/remember/mcp\_tools/\_\_init\_\_.py                                                       |       47 |        0 |        0 |        0 |    100.0% |           |
| src/remember/mcp\_tools/\_definitions.py                                                      |      136 |        0 |        8 |        0 |    100.0% |           |
| src/remember/mcp\_tools/\_documents.py                                                        |       46 |        1 |        6 |        1 |     96.2% |       117 |
| src/remember/mcp\_tools/\_errors.py                                                           |       97 |        5 |       44 |        5 |     92.9% |93, 200, 210, 221, 223 |
| src/remember/mcp\_tools/\_memory.py                                                           |      391 |       46 |      136 |       26 |     85.2% |109, 111-119, 122, 209, 261, 303, 354, 390, 572, 601, 663, 684, 700, 734-735, 751, 865-866, 927, 942-945, 962, 976, 994-995, 1005, 1016, 1027, 1117, 1131, 1138-1139, 1237, 1241, 1245, 1259 |
| src/remember/mcp\_tools/\_query.py                                                            |       95 |        5 |       56 |        6 |     92.7% |37, 42, 55, 112-\>133, 126, 193 |
| src/remember/mcp\_tools/\_references.py                                                       |       48 |        4 |        8 |        2 |     89.3% |94, 117-120, 123 |
| src/remember/mcp\_tools/\_sections.py                                                         |       47 |        4 |        8 |        2 |     89.1% |81, 99-102, 105 |
| src/remember/mcp\_tools/\_tags.py                                                             |      113 |        6 |       30 |        3 |     93.7% |76, 78, 90, 110, 194-195 |
| src/remember/mcp\_tools/\_validate.py                                                         |       82 |        5 |       30 |        4 |     92.0% |50, 61, 114-115, 119 |
| src/remember/mime.py                                                                          |       10 |        0 |        2 |        0 |    100.0% |           |
| src/remember/models.py                                                                        |     1044 |       15 |       70 |        6 |     97.4% |39, 101-104, 497, 1362-1364, 1521-1523, 1603, 1799, 1801 |
| src/remember/mount.py                                                                         |      121 |        6 |       28 |        1 |     95.3% |141-142, 145, 172-174 |
| src/remember/query\_sandbox/\_\_init\_\_.py                                                   |        0 |        0 |        0 |        0 |    100.0% |           |
| src/remember/query\_sandbox/errors.py                                                         |       35 |        0 |        0 |        0 |    100.0% |           |
| src/remember/query\_sandbox/result.py                                                         |       70 |        0 |        0 |        0 |    100.0% |           |
| src/remember/retrieval.py                                                                     |       29 |        0 |        4 |        0 |    100.0% |           |
| src/remember/setup.py                                                                         |      427 |       39 |      142 |       11 |     90.2% |154-159, 322-324, 418-\>420, 431, 440-441, 453-458, 486-487, 490, 512-513, 515-516, 582-583, 587-588, 716-725, 768-769, 775, 891-892 |
| src/rememberstack/\_\_init\_\_.py                                                             |        9 |        5 |        0 |        0 |     44.4% |      8-12 |
| src/rememberstack/adapters/\_\_init\_\_.py                                                    |       58 |       12 |       14 |        6 |     75.0% |105-109, 115-117, 119-123, 125-129, 131-133, 135-137 |
| src/rememberstack/adapters/bounded\_postgres\_read.py                                         |       81 |        8 |       26 |        8 |     85.0% |26, 28, 47-\>49, 58, 82, 116, 126, 143, 156 |
| src/rememberstack/adapters/codex\_subscription.py                                             |      196 |       23 |       60 |       12 |     85.5% |80, 82, 84, 86, 185, 245, 248, 318, 349-350, 357-358, 366-\>370, 383, 420-426, 438, 449, 453 |
| src/rememberstack/adapters/codex\_writer.py                                                   |       82 |        3 |       20 |        3 |     94.1% |178, 209, 221 |
| src/rememberstack/adapters/converters/\_\_init\_\_.py                                         |       72 |        2 |       14 |        2 |     95.3% |    43, 61 |
| src/rememberstack/adapters/converters/card.py                                                 |        5 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/converters/dataset.py                                              |      156 |        6 |       14 |        3 |     94.7% |82, 108, 234, 265-266, 283 |
| src/rememberstack/adapters/converters/email\_message.py                                       |       98 |        9 |       20 |        3 |     89.8% |57, 124-125, 127, 134-135, 168-169, 170-\>174, 172 |
| src/rememberstack/adapters/converters/image\_ocr\_description.py                              |      561 |       37 |      168 |       24 |     91.4% |192, 202, 213, 273-\>exit, 573, 643-644, 683-684, 691, 818, 892, 904, 944, 952, 962, 967, 990, 993, 1011, 1014, 1017, 1020-1028, 1071, 1085, 1115-\>1118, 1158-\>1160, 1288, 1308-1309, 1313, 1320-1321, 1325 |
| src/rememberstack/adapters/converters/image\_png.py                                           |      162 |      125 |       54 |        1 |     19.4% |149-255, 265-285, 297-310, 320-325, 333-346, 351-385, 389 |
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
| src/rememberstack/adapters/fsit\_storage\_capture.py                                          |      286 |       53 |       78 |       24 |     74.5% |37, 44, 84, 89, 293, 339, 358, 365, 368, 379, 386, 394, 400, 406-410, 415-418, 427, 438, 446, 454, 460, 470, 484, 513, 541-571, 579, 583, 587 |
| src/rememberstack/adapters/fsit\_storage\_capture\_transport.py                               |      273 |       48 |       46 |       16 |     79.9% |134, 146, 151-152, 158, 168, 198-199, 206, 209-210, 217, 239, 273-274, 277-281, 283, 323-324, 328, 334, 336-337, 344-346, 348-350, 360-361, 370, 385-386, 388, 391-392, 397-399, 410-411, 421-422, 424, 450 |
| src/rememberstack/adapters/generation\_recorder.py                                            |      134 |       17 |       14 |        4 |     85.8% |193-196, 211-231, 269-270, 288-\>290, 290-\>295, 299, 302-\>309 |
| src/rememberstack/adapters/managed/\_\_init\_\_.py                                            |        0 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/managed/composite\_auth.py                                         |       22 |        2 |        4 |        0 |     92.3% |     53-54 |
| src/rememberstack/adapters/managed/perimeter\_trust.py                                        |      205 |       16 |       72 |       12 |     89.9% |122, 145, 169, 241, 246, 303-\>305, 348-349, 402, 434-435, 437, 444-445, 458, 482, 493, 501-\>exit |
| src/rememberstack/adapters/managed/signed\_token\_auth.py                                     |      136 |        1 |       62 |        1 |     99.0% |       170 |
| src/rememberstack/adapters/openai\_decisions.py                                               |      208 |       30 |       86 |       24 |     81.0% |73, 78, 105, 109, 143-144, 149-154, 158-166, 196, 205, 207, 224-\>240, 230, 256, 268, 273, 280-\>286, 313, 316, 326, 330, 335, 340, 355, 359, 389, 394 |
| src/rememberstack/adapters/openrouter.py                                                      |      749 |       52 |      270 |       25 |     92.1% |87-88, 265, 683-684, 694, 713-718, 815-827, 875, 886, 900, 921-924, 925-\>934, 930-\>934, 933, 1061, 1121, 1216-1217, 1224-\>1226, 1230-1231, 1233, 1246-\>1248, 1255-1256, 1322, 1328-1332, 1425-1426, 1447-1448, 1450, 1454-\>1456, 1457-\>1466, 1470, 1495-1496, 1498, 1517, 1675-1676, 1680, 1722 |
| src/rememberstack/adapters/p1\_ranked.py                                                      |      195 |        8 |       56 |        5 |     94.8% |288, 347-349, 359-\>419, 381-382, 384, 473 |
| src/rememberstack/adapters/postgres\_p1.py                                                    |      760 |       72 |      228 |       39 |     87.1% |258, 298, 312, 344, 373, 406, 444, 484, 587, 661, 811-\>813, 975-978, 1053, 1091, 1132, 1134, 1222, 1236, 1260, 1269, 1380, 1408, 1749, 1774, 1847-1867, 2012, 2017, 2024-2028, 2148, 2263-2279, 2299, 2321, 2357-2372, 2401, 2420, 2469, 2616, 2632, 2679, 2727, 2733-2734, 2753 |
| src/rememberstack/adapters/routed.py                                                          |       19 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/\_\_init\_\_.py                                           |       21 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/control\_plane\_spend\_lease.py                           |       65 |       18 |       22 |        8 |     70.1% |43, 45, 49-50, 75, 87, 94-97, 106, 108, 111-112, 114, 122-123, 128 |
| src/rememberstack/adapters/selfhost/git.py                                                    |      319 |       14 |       94 |       11 |     93.9% |71, 112, 117-\>130, 194-\>202, 307-\>309, 326, 338-\>342, 533, 631-633, 648-650, 666, 742-\>737, 755-756, 817 |
| src/rememberstack/adapters/selfhost/hashed\_bearer\_auth.py                                   |       47 |        8 |       12 |        4 |     79.7% |27, 46, 64-65, 70, 73-74, 76 |
| src/rememberstack/adapters/selfhost/managed\_metering.py                                      |       45 |        8 |       12 |        3 |     80.7% |42-43, 52-\>exit, 59, 66-67, 78-79, 81 |
| src/rememberstack/adapters/selfhost/minio.py                                                  |      408 |      124 |      170 |       26 |     64.7% |237, 239, 250, 253-258, 271, 285, 305, 332-398, 410-422, 428-436, 440-448, 488, 491-493, 546, 552, 554, 563, 564-\>561, 569, 576, 580, 586, 608-612, 616-620, 633-653, 663-664, 667, 683, 697-698, 714-721, 726, 736, 757, 761, 770-771, 781, 790-\>exit, 867 |
| src/rememberstack/adapters/selfhost/mounts.py                                                 |       28 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/object\_store.py                                          |      170 |        6 |       72 |        8 |     94.2% |117-\>120, 167-\>169, 209, 211, 218, 220, 226, 229 |
| src/rememberstack/adapters/selfhost/projection.py                                             |       19 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/adapters/selfhost/queue.py                                                  |      122 |        8 |       30 |        3 |     92.8% |118, 189, 210-214, 226 |
| src/rememberstack/adapters/selfhost/telemetry.py                                              |       43 |        7 |        6 |        1 |     79.6% | 58, 63-68 |
| src/rememberstack/adapters/selfhost/watcher.py                                                |       31 |        1 |       10 |        1 |     95.1% |        39 |
| src/rememberstack/adapters/sentry.py                                                          |       74 |        9 |       28 |        6 |     85.3% |119-125, 152-\>160, 154-\>160, 157, 166, 169, 172 |
| src/rememberstack/adapters/systemone.py                                                       |       49 |        7 |       12 |        1 |     86.9% |47, 113-118 |
| src/rememberstack/adapters/testing/\_\_init\_\_.py                                            |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/cost\_meter.py                                             |        4 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/model\_provider.py                                         |       35 |        1 |        4 |        1 |     94.9% |        50 |
| src/rememberstack/adapters/testing/profile\_refresher.py                                      |       15 |        2 |        0 |        0 |     86.7% |     25-26 |
| src/rememberstack/adapters/testing/queue.py                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/testing/telemetry.py                                               |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/adapters/typesafe.py                                                        |      143 |        9 |       44 |        3 |     92.5% |89-90, 255-256, 269, 275-277, 317 |
| src/rememberstack/adapters/vertex.py                                                          |      323 |       46 |       98 |       16 |     83.8% |210-245, 516-\>487, 563-571, 578-579, 581, 589-590, 598-601, 668, 682, 691, 693, 700, 707, 710, 712, 750, 759-\>747, 761-\>763, 765-767, 795, 817 |
| src/rememberstack/client.py                                                                   |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/\_\_init\_\_.py                                                        |      101 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/assured\_operation\_linter.py                                          |       33 |        4 |       18 |        4 |     84.3% |58, 81, 88, 93 |
| src/rememberstack/core/blockizer.py                                                           |       93 |        5 |       36 |        5 |     92.2% |202, 217, 246, 252, 254 |
| src/rememberstack/core/chunker.py                                                             |       76 |        0 |       22 |        0 |    100.0% |           |
| src/rememberstack/core/concise\_adjudication.py                                               |      309 |        8 |      146 |       16 |     94.7% |74, 83-\>81, 87-\>85, 91-\>89, 132, 145, 212-\>222, 302-\>305, 342-\>347, 356, 416, 422, 425-\>430, 461-\>463, 505, 600 |
| src/rememberstack/core/consumption\_skill.py                                                  |       77 |        0 |       10 |        1 |     98.9% | 304-\>306 |
| src/rememberstack/core/content\_detection.py                                                  |      415 |       31 |      186 |       20 |     89.9% |112, 187, 221, 232-235, 244, 348, 369, 375-377, 386-\>402, 402-\>428, 419-\>428, 466, 469, 478, 493, 517, 522, 524, 673-680, 689, 771, 808 |
| src/rememberstack/core/context\_references.py                                                 |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/conversion.py                                                          |       85 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/core/core\_manifest.py                                                      |       21 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/corpus\_export.py                                                      |      216 |        4 |       68 |        4 |     97.2% |120, 322, 534, 652 |
| src/rememberstack/core/cursor\_scope.py                                                       |       18 |        5 |        6 |        0 |     62.5% |     40-44 |
| src/rememberstack/core/document\_filters.py                                                   |      112 |        2 |       32 |        0 |     98.6% |   148-149 |
| src/rememberstack/core/document\_metadata.py                                                  |       19 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/core/embedding\_input\_policy.py                                            |      191 |       13 |       74 |        9 |     89.4% |140-\>149, 159, 215-216, 258, 284-290, 310-\>312, 332, 333-\>335, 341 |
| src/rememberstack/core/entity\_profile\_input.py                                              |        5 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/extension\_packs.py                                                    |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/extraction\_eligibility.py                                             |       22 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/core/fact\_application.py                                                   |       44 |        6 |       28 |        6 |     83.3% |52, 62, 64, 76, 78, 82 |
| src/rememberstack/core/fact\_label.py                                                         |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/fact\_windows.py                                                       |       71 |        4 |       38 |        4 |     92.7% |55, 57, 69, 146 |
| src/rememberstack/core/file\_card.py                                                          |      150 |        3 |       36 |        2 |     97.3% |118, 254, 269 |
| src/rememberstack/core/forget.py                                                              |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/format\_registry.py                                                    |       94 |        0 |       28 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_authored.py                                                 |      170 |       23 |       72 |       12 |     85.5% |116-117, 141, 160, 167, 172-173, 185, 193-196, 216-217, 223, 233-234, 239, 245-246, 250, 252, 255, 261-\>263 |
| src/rememberstack/core/knowledge\_compile.py                                                  |      106 |       11 |       52 |        7 |     86.1% |39, 44, 129, 131, 170-172, 184-186, 202 |
| src/rememberstack/core/knowledge\_fact\_sheet.py                                              |       77 |        5 |       30 |        5 |     90.7% |40, 82, 101, 149, 218 |
| src/rememberstack/core/knowledge\_hashing.py                                                  |       17 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/knowledge\_planner.py                                                  |       33 |        4 |       14 |        4 |     83.0% |33, 35, 48, 50 |
| src/rememberstack/core/knowledge\_writer.py                                                   |       71 |        1 |       30 |        1 |     98.0% |        24 |
| src/rememberstack/core/metering.py                                                            |       31 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/core/object\_keys.py                                                        |       10 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/core/object\_transfer.py                                                    |       23 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/core/open\_query\_prose.py                                                  |       20 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/core/ranking.py                                                             |       66 |        8 |       20 |        6 |     83.7% |60, 125, 127, 166, 178-179, 186, 197 |
| src/rememberstack/core/section\_snap.py                                                       |       63 |        3 |       26 |        3 |     93.3% |122, 177, 203 |
| src/rememberstack/core/selection\_references.py                                               |      149 |       11 |       50 |       11 |     88.9% |117, 126, 138, 233, 235, 246, 267, 358, 361, 365, 368 |
| src/rememberstack/core/source\_origins.py                                                     |       45 |        0 |       10 |        0 |    100.0% |           |
| src/rememberstack/core/source\_passages.py                                                    |      170 |       20 |       66 |       13 |     83.5% |56, 142-144, 157, 182, 209, 266, 291, 296, 306, 308, 311, 345, 348-349, 356-358, 401 |
| src/rememberstack/core/storage\_capture.py                                                    |      114 |       15 |       54 |       15 |     82.1% |94, 143, 145, 148, 153, 155, 157, 165, 167, 170, 172, 174, 176, 188, 227 |
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
| src/rememberstack/model/\_\_init\_\_.py                                                       |      390 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/adjudication.py                                                       |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/assured\_operations.py                                                |       70 |        1 |        6 |        1 |     97.4% |       133 |
| src/rememberstack/model/auth.py                                                               |       42 |        0 |        8 |        0 |    100.0% |           |
| src/rememberstack/model/blocks.py                                                             |       28 |        2 |        4 |        2 |     87.5% |    41, 47 |
| src/rememberstack/model/chunks.py                                                             |      109 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/claims.py                                                             |      142 |        1 |       12 |        1 |     98.7% |       179 |
| src/rememberstack/model/client.py                                                             |       57 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/clustering.py                                                         |       22 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/component\_version.py                                                 |       65 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/model/concise\_adjudication.py                                              |       61 |        6 |       16 |        6 |     84.4% |52, 114, 126, 131, 139, 141 |
| src/rememberstack/model/consumption.py                                                        |       29 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/content\_detection.py                                                 |        4 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/conversion.py                                                         |      171 |       11 |       22 |        2 |     90.2% |111, 206, 299-301, 328-330, 348-350 |
| src/rememberstack/model/deployment.py                                                         |       16 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/document\_metadata.py                                                 |       26 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/documents.py                                                          |      164 |        3 |        6 |        2 |     97.1% |151, 153, 307 |
| src/rememberstack/model/envelope.py                                                           |      240 |        2 |       10 |        2 |     98.4% |  109, 733 |
| src/rememberstack/model/evaluation.py                                                         |       27 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/fact\_application.py                                                  |       61 |        6 |       16 |        6 |     84.4% |32, 97, 109, 116, 124, 126 |
| src/rememberstack/model/fact\_windows.py                                                      |       60 |        4 |       28 |        4 |     90.9% |80, 85, 90, 119 |
| src/rememberstack/model/forget.py                                                             |       31 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/git.py                                                                |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/knowledge.py                                                          |      369 |       23 |       38 |       13 |     89.2% |216, 255, 339, 351, 370, 382, 412, 571, 588, 616-627, 639-641, 670, 685, 706, 753 |
| src/rememberstack/model/knowledge\_authored.py                                                |      135 |        2 |        8 |        1 |     97.9% |   29, 192 |
| src/rememberstack/model/knowledge\_planner.py                                                 |      212 |       15 |       32 |       11 |     88.5% |47, 106, 139, 145, 160, 189, 194, 196, 238, 275-277, 291, 312, 379 |
| src/rememberstack/model/lifecycle.py                                                          |       15 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/metering.py                                                           |       94 |        2 |       10 |        2 |     96.2% |  102, 154 |
| src/rememberstack/model/model\_provider.py                                                    |       45 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/mounts.py                                                             |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/object\_store.py                                                      |       12 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/occurrence\_provenance.py                                             |      179 |       11 |       48 |        8 |     91.6% |150-151, 165, 168-\>174, 195, 270, 289-290, 340, 385, 403, 421 |
| src/rememberstack/model/operational\_scale.py                                                 |       25 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/operations.py                                                         |       50 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/processing.py                                                         |       97 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/processing\_layers.py                                                 |       65 |        0 |       16 |        0 |    100.0% |           |
| src/rememberstack/model/queue.py                                                              |       48 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/relations.py                                                          |       44 |        0 |        4 |        0 |    100.0% |           |
| src/rememberstack/model/resolution.py                                                         |       46 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/model/retrieval\_spikes.py                                                  |       26 |        7 |        2 |        0 |     67.9% | 49-56, 61 |
| src/rememberstack/model/sections.py                                                           |      190 |        3 |       10 |        3 |     97.0% |293, 297, 303 |
| src/rememberstack/model/spend\_lease.py                                                       |       42 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/model/storage\_capture.py                                                   |      108 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/model/telemetry.py                                                          |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/\_\_init\_\_.py                                                       |       18 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/auth.py                                                               |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/connector.py                                                          |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/cost\_meter.py                                                        |        6 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/git.py                                                                |       10 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/metering.py                                                           |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/model\_provider.py                                                    |       13 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/mounts.py                                                             |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/object\_store.py                                                      |       24 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/p1\_index.py                                                          |      118 |        2 |       18 |        2 |     97.1% |  321, 328 |
| src/rememberstack/ports/postgres\_read.py                                                     |       11 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/profile\_refresher.py                                                 |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/purge.py                                                              |       24 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/queue.py                                                              |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/storage\_capture.py                                                   |       18 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/systemone.py                                                          |        8 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/ports/telemetry.py                                                          |        7 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/profiles/\_\_init\_\_.py                                                    |        7 |        1 |        2 |        1 |     77.8% |        20 |
| src/rememberstack/profiles/managed\_storage\_capture.py                                       |      429 |      253 |      160 |        4 |     32.3% |141-158, 189, 239-247, 256-327, 331-343, 351-365, 376-418, 426-437, 452-455, 464-528, 548-596, 617-641, 647-670, 688-799, 803-818, 822-830, 845-855, 870-934, 945-962, 972-983, 1001, 1007, 1020, 1023, 1048, 1055-1059, 1064 |
| src/rememberstack/profiles/selfhost.py                                                        |      910 |      153 |      190 |       24 |     82.1% |306, 341, 346, 350, 354, 409, 434, 563-564, 661, 678, 707, 717, 725, 733-749, 827-857, 870-880, 924, 1062, 1146-1164, 1171-1301, 1345, 1532-1537, 1545-1552, 1569-1582, 1628, 1633, 1687-1689, 1748, 1979, 1997-2001, 2029-2066, 2074, 2078, 2093, 2101, 2196-2206, 2229, 2425 |
| src/rememberstack/profiles/selfhost\_forget.py                                                |       68 |       10 |        4 |        1 |     81.9% |122-125, 129, 138, 145, 167-177, 181 |
| src/rememberstack/profiles/selfhost\_operations.py                                            |       38 |        6 |        0 |        0 |     84.2% |42, 59-62, 76-77 |
| src/rememberstack/profiles/storage\_capture\_batch.py                                         |      140 |       28 |       32 |        2 |     79.1% |72-101, 120-121, 199, 228, 238 |
| src/rememberstack/profiles/storage\_capture\_profile.py                                       |       97 |        4 |       12 |        4 |     92.7% |265, 277, 334, 392 |
| src/rememberstack/spine/\_\_init\_\_.py                                                       |       57 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/apply\_fact\_decision.py                                              |      137 |       12 |       36 |        3 |     89.0% |50, 78, 231-256 |
| src/rememberstack/spine/assured\_operations.py                                                |       88 |        3 |        8 |        1 |     95.8% | 40-41, 93 |
| src/rememberstack/spine/backfill.py                                                           |       28 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/catalog\_contract.py                                                  |      161 |       24 |       74 |       24 |     79.6% |524, 549, 562, 617, 636, 664, 677, 718, 731, 746, 768, 792, 809, 820, 840, 896, 936, 938, 940, 942, 944, 946, 948, 1000 |
| src/rememberstack/spine/chunk\_catalog.py                                                     |       84 |        3 |       14 |        3 |     93.9% |41, 98, 255 |
| src/rememberstack/spine/claim\_catalog.py                                                     |      147 |       12 |       50 |        8 |     87.8% |161-163, 179, 199, 250, 258, 320, 337-339, 631 |
| src/rememberstack/spine/clustering.py                                                         |      290 |       14 |      108 |       17 |     92.2% |67, 142, 202-\>180, 248, 318, 387, 468-\>447, 534-\>523, 615, 617, 628, 633, 676, 679, 698, 799, 804 |
| src/rememberstack/spine/component\_versions.py                                                |       56 |        3 |       12 |        3 |     91.2% |102, 119, 187 |
| src/rememberstack/spine/consumption.py                                                        |       20 |        1 |        2 |        1 |     90.9% |        32 |
| src/rememberstack/spine/corpus\_export.py                                                     |       23 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/corpus\_publication.py                                                |      119 |       11 |       24 |        8 |     86.7% |82, 117, 137-138, 142, 224-\>exit, 232-235, 278, 315-321, 343, 370-\>345 |
| src/rememberstack/spine/cost\_export.py                                                       |      212 |       15 |       40 |        8 |     90.9% |96, 192, 283, 302-303, 336, 344-345, 359, 377, 387-388, 390, 397, 430 |
| src/rememberstack/spine/deployment\_bootstrap.py                                              |       84 |        1 |       20 |        1 |     98.1% |       245 |
| src/rememberstack/spine/document\_bindings.py                                                 |      105 |        7 |       34 |        7 |     89.9% |91-\>109, 112-\>114, 118, 152, 162, 169, 174-175, 220 |
| src/rememberstack/spine/document\_catalog.py                                                  |      248 |        6 |       44 |        6 |     95.9% |485, 570, 599, 816, 827-\>867, 937-945 |
| src/rememberstack/spine/document\_inventory.py                                                |       51 |        1 |        6 |        1 |     96.5% |       248 |
| src/rememberstack/spine/document\_metadata.py                                                 |       72 |        3 |       18 |        3 |     93.3% |112, 152, 274 |
| src/rememberstack/spine/document\_references.py                                               |      489 |       24 |      166 |       25 |     92.2% |112, 200, 212, 222, 399, 403-\>397, 454, 456, 458, 474-\>476, 492, 506, 582, 613, 692, 708, 771-\>779, 886, 914-919, 959, 1083, 1135, 1136-\>1140, 1139, 1144 |
| src/rememberstack/spine/document\_search.py                                                   |      328 |        3 |       92 |        4 |     98.3% |588, 592-\>583, 821, 1302 |
| src/rememberstack/spine/effective\_time.py                                                    |      128 |        1 |       38 |        1 |     98.8% |        95 |
| src/rememberstack/spine/entity\_eligibility.py                                                |       29 |        3 |       12 |        3 |     85.4% |48, 60, 89 |
| src/rememberstack/spine/entity\_registry.py                                                   |       49 |        6 |        4 |        1 |     83.0% |64-\>84, 121, 125-131 |
| src/rememberstack/spine/extension\_packs.py                                                   |       40 |        1 |       16 |        1 |     96.4% |       119 |
| src/rememberstack/spine/fact\_adjudication.py                                                 |      312 |       20 |      112 |       24 |     89.6% |331, 338-\>303, 424-425, 428, 430, 490, 505-\>512, 526, 530, 534, 587-\>592, 588-\>587, 598-\>652, 610-\>614, 611-\>610, 624, 628-\>652, 666-667, 698, 798, 813, 825, 827, 882, 932-933, 1016-\>1035 |
| src/rememberstack/spine/fact\_application\_inputs.py                                          |      126 |        6 |       40 |        7 |     92.2% |66, 94, 264, 293, 442, 533, 537-\>534 |
| src/rememberstack/spine/fact\_applications.py                                                 |      111 |        6 |       28 |        6 |     91.4% |53, 80, 175, 183, 239, 506 |
| src/rememberstack/spine/fact\_catalog.py                                                      |      155 |       27 |       22 |        4 |     80.2% |49, 64, 90-91, 104-\>exit, 124, 310-\>312, 335, 346-355, 361-362, 380-381, 405-424, 430-442, 473-484 |
| src/rememberstack/spine/fact\_graph\_contract.py                                              |       50 |        0 |       18 |        0 |    100.0% |           |
| src/rememberstack/spine/fact\_window\_review.py                                               |      174 |        8 |       40 |        7 |     93.0% |193, 209, 269, 304, 394-403, 500, 575 |
| src/rememberstack/spine/forget.py                                                             |      356 |       10 |       52 |        5 |     95.8% |135, 214-219, 404-405, 546-547, 634-\>exit, 1083 |
| src/rememberstack/spine/graph\_catalog.py                                                     |       92 |       13 |       34 |       11 |     81.0% |95-98, 110, 148, 152-\>154, 156, 197, 280-281, 284, 301-302, 314, 393 |
| src/rememberstack/spine/knowledge.py                                                          |     1292 |      116 |      474 |      101 |     87.1% |153, 168, 239, 249, 277, 283-\>exit, 296, 307, 319, 323, 377, 419, 453, 514-519, 557, 625-634, 649, 668, 696-\>692, 763, 777, 888, 939, 969, 1071, 1096, 1130, 1232, 1283, 1319, 1328, 1383, 1403, 1434, 1488, 1517, 1522, 1524, 1526, 1582, 1584, 1586, 1588, 1590, 1607, 1614, 1618, 1649, 1660, 1662-\>1680, 1719, 1835, 1864, 1882, 1907-1913, 2019-2022, 2131, 2155, 2178, 2219, 2226, 2228-2229, 2236, 2249, 2274-2287, 2348, 2382, 2400, 2410, 2437, 2439, 2443, 2452, 2477, 2505, 2634, 2645, 2649, 2666, 2669, 2741, 2772, 2789, 2817, 2841, 2847-\>2858, 2858-\>2869, 2894, 2910, 2987-\>2992, 3016-3020, 3135, 3139-\>3152, 3152-\>3164, 3164-\>3171, 3207, 3230-3236, 3277-\>3287, 3287-\>3300, 3415-3419, 3480-3489, 3525, 3635, 3654 |
| src/rememberstack/spine/lifecycle.py                                                          |      387 |        4 |       44 |        4 |     98.1% |184, 576, 615, 966 |
| src/rememberstack/spine/managed\_metering.py                                                  |      214 |        8 |       40 |        6 |     94.5% |129, 212, 214, 299-307, 382-\>295, 492-\>437 |
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
| src/rememberstack/spine/migrations/versions/p9\_54\_0075\_forget\_records.py                  |       64 |        4 |       14 |        2 |     89.7% |107, 157-159 |
| src/rememberstack/spine/migrations/versions/p9\_55\_0076\_archive.py                          |      123 |        8 |       26 |        8 |     89.3% |175, 205, 232, 322, 344, 600, 609, 628 |
| src/rememberstack/spine/new\_work.py                                                          |       36 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/spine/observation\_adjudication.py                                          |       98 |       27 |       18 |        5 |     65.5% |132, 148, 161, 201, 209-221, 251, 256, 261, 271, 281-296, 301-304, 319 |
| src/rememberstack/spine/operations.py                                                         |       81 |        1 |        4 |        1 |     97.6% |       161 |
| src/rememberstack/spine/perimeter\_state.py                                                   |       24 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/period\_adjudication.py                                               |      116 |        2 |       26 |        2 |     97.2% |  197, 676 |
| src/rememberstack/spine/postgres\_graph\_sql.py                                               |       20 |        0 |        2 |        0 |    100.0% |           |
| src/rememberstack/spine/processing\_layers.py                                                 |      361 |       14 |      122 |        8 |     95.0% |165-172, 407-408, 510-516, 635, 737, 738-\>745, 977, 1038, 1174, 1188 |
| src/rememberstack/spine/profile\_convergence.py                                               |       24 |        6 |        0 |        0 |     75.0% |30-37, 48-55 |
| src/rememberstack/spine/profile\_refresher.py                                                 |      247 |       14 |       82 |       14 |     91.5% |112, 124-\>100, 126, 191, 202, 222, 279, 302-303, 354, 396, 487, 546, 579, 671, 707-\>681 |
| src/rememberstack/spine/query\_space/\_\_init\_\_.py                                          |       40 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/ast\_serializer.py                                       |       27 |        4 |       10 |        2 |     83.8% |77-78, 82, 102 |
| src/rememberstack/spine/query\_space/canonical.py                                             |       71 |        2 |       32 |        1 |     97.1% |   89, 137 |
| src/rememberstack/spine/query\_space/catalog.py                                               |       31 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/deletion\_matrix.py                                      |       80 |        2 |       18 |        1 |     96.9% |  574, 580 |
| src/rememberstack/spine/query\_space/manifest.py                                              |      192 |       18 |       48 |       13 |     87.1% |159, 657, 673, 719, 808, 814, 822, 892, 972, 989, 996-999, 1004, 1009, 1020, 1031, 1171, 1173 |
| src/rememberstack/spine/query\_space/quarantine.py                                            |       20 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/query\_space/source\_definitions.py                                   |      124 |        4 |       32 |        4 |     94.9% |203, 270, 273, 295 |
| src/rememberstack/spine/rank\_embed\_cache.py                                                 |      105 |       11 |       30 |        9 |     85.2% |44, 46, 48, 50, 89, 101, 125-127, 148-\>152, 175, 181 |
| src/rememberstack/spine/readiness.py                                                          |      217 |       25 |       76 |       10 |     85.3% |86, 575-583, 599, 642, 644-\>658, 651-\>644, 659, 683, 685, 693, 695, 699-704, 707-712 |
| src/rememberstack/spine/references.py                                                         |      233 |        6 |       44 |        4 |     96.4% |358-359, 368, 506, 535, 743 |
| src/rememberstack/spine/resolver.py                                                           |      356 |       15 |       96 |       11 |     94.2% |519, 548, 865-868, 876-883, 893-898, 938, 1014, 1055, 1224, 1236, 1241, 1299 |
| src/rememberstack/spine/retrieval\_eligibility.py                                             |       22 |        1 |        4 |        1 |     92.3% |       198 |
| src/rememberstack/spine/review.py                                                             |      196 |       14 |       58 |       14 |     89.0% |142, 250, 305, 346, 387-\>398, 495-503, 551, 553, 620, 624, 642, 655, 684, 943 |
| src/rememberstack/spine/section\_history.py                                                   |      207 |        4 |       54 |        4 |     96.9% |299, 428, 489, 642 |
| src/rememberstack/spine/section\_index\_backfill.py                                           |       73 |        7 |       10 |        3 |     88.0% |91, 110-116, 133-138, 157 |
| src/rememberstack/spine/selection\_catalog.py                                                 |       54 |        3 |        8 |        3 |     90.3% |200, 209, 212 |
| src/rememberstack/spine/settings.py                                                           |        9 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/spine/storage\_capture\_catalog.py                                          |       79 |        3 |        6 |        3 |     92.9% |291, 303, 338 |
| src/rememberstack/spine/storage\_capture\_guard.py                                            |       75 |        7 |       14 |        7 |     84.3% |294, 296, 305, 307, 324, 331, 380 |
| src/rememberstack/spine/storage\_capture\_view.py                                             |      227 |      152 |       64 |        0 |     25.8% |57-58, 112-121, 130-145, 149-171, 177-192, 196-197, 201-204, 208-220, 226-227, 240-241, 254-398, 403-414, 421-432, 437-447, 467-475, 482-486, 497-509 |
| src/rememberstack/spine/supersession.py                                                       |       13 |        1 |        0 |        0 |     92.3% |        36 |
| src/rememberstack/spine/surface\_cost.py                                                      |      137 |       23 |       12 |        4 |     80.5% |101-102, 139, 163-168, 171-176, 203-208, 212-225, 269-270 |
| src/rememberstack/spine/sync.py                                                               |       32 |        0 |        2 |        1 |     97.1% |   94-\>98 |
| src/rememberstack/spine/tags.py                                                               |      134 |        1 |       24 |        1 |     98.7% |       344 |
| src/rememberstack/spine/work\_ledger.py                                                       |      537 |       42 |      174 |       37 |     88.3% |90, 161, 281, 283, 307, 337, 339, 359, 424, 426, 444, 507, 509, 558, 580, 636, 638, 648, 673, 675, 740-747, 775, 817, 935, 961, 1022, 1033, 1051, 1099, 1283, 1295-\>1310, 1366, 1511, 1522, 1532, 1701, 1772, 1896-\>1911 |
| src/rememberstack/surfaces/\_\_init\_\_.py                                                    |       14 |        2 |        0 |        0 |     85.7% |   101-102 |
| src/rememberstack/surfaces/consumption\_skill.py                                              |       42 |        3 |        8 |        2 |     90.0% |35, 67, 86 |
| src/rememberstack/surfaces/cost\_export\_api.py                                               |      112 |       37 |       24 |        1 |     63.2% |121-125, 146-162, 167-192, 201 |
| src/rememberstack/surfaces/direct\_admission.py                                               |      134 |        1 |       46 |        2 |     98.3% |167, 174-\>exit |
| src/rememberstack/surfaces/graph\_queries.py                                                  |      403 |       36 |      132 |       33 |     86.7% |88, 106, 108, 110, 139, 161, 301, 360, 371, 425, 427, 474-475, 488, 491, 500, 534-\>537, 733, 739, 773-778, 831, 835, 844, 894, 921, 959, 992, 1009, 1111, 1113, 1179, 1193, 1204, 1218, 1220 |
| src/rememberstack/surfaces/http\_api.py                                                       |     1103 |       47 |      258 |       12 |     95.1% |509, 845, 896, 1594-1600, 1656-1662, 1669-1697, 1763, 1877, 1968-1969, 2164, 2257, 2342-2343, 2398, 2449-\>2453, 2499, 2505-2506, 2720, 2722, 2828-2829, 2863-2864, 2953, 3198, 3290-\>3293, 3308-3309, 3334, 3363-3367, 3389-3393 |
| src/rememberstack/surfaces/mcp.py                                                             |      314 |       21 |       58 |        6 |     92.7% |210-211, 227-228, 264-265, 289-290, 545, 560-561, 576-577, 673, 683-686, 697-700, 716-721, 752 |
| src/rememberstack/surfaces/operation\_executor.py                                             |       57 |        1 |        8 |        1 |     96.9% |       194 |
| src/rememberstack/surfaces/operation\_surface.py                                              |      145 |       15 |       68 |        9 |     86.9% |208, 220, 230, 243-247, 256, 265-266, 283, 289, 292, 294 |
| src/rememberstack/surfaces/query\_engine.py                                                   |     1742 |      153 |      524 |       77 |     88.6% |289, 294-301, 303, 359-360, 590, 602-611, 682-687, 816, 860, 907, 924, 930-\>926, 1002, 1018-1022, 1072, 1076, 1271, 1374-1377, 1424, 1427, 1429, 1443, 1498, 1844, 1858-1860, 1881-1890, 1899, 1926-1927, 1963, 1974, 1993-1995, 2019, 2185, 2203, 2205-2208, 2323, 2426, 2435, 2483, 2510, 2653-2656, 2703-2707, 2804, 2961-2965, 3061, 3125, 3177, 3199-3201, 3239, 3260-\>3262, 3263, 3303, 3325, 3364, 3411, 3497, 3573-\>3575, 3576, 3932-3961, 4022-4052, 4087, 4130, 4214, 4313-4330, 4335-\>4349, 4493, 4495, 4519-4523, 4575, 4677, 4709, 4754, 4773-4780, 4818, 4827, 4850-4858, 4893-\>4895, 4901, 4903, 4911, 4925, 4955, 4997, 5010, 5049, 5051, 5069, 5114, 5249, 5553, 5557, 5890, 5983, 6880 |
| src/rememberstack/surfaces/query\_sandbox/\_\_init\_\_.py                                     |       21 |       14 |        6 |        0 |     25.9% |     50-66 |
| src/rememberstack/surfaces/query\_sandbox/audit.py                                            |       92 |        7 |       14 |        4 |     89.6% |109-\>116, 153-154, 157-158, 178, 203, 207 |
| src/rememberstack/surfaces/query\_sandbox/bridge.py                                           |      302 |       25 |       98 |       15 |     89.0% |223, 231-232, 239, 285, 301, 315, 366, 442, 475, 578, 675, 691, 716, 728-729, 822, 848, 931, 942-947 |
| src/rememberstack/surfaces/query\_sandbox/discovery.py                                        |      106 |        3 |       24 |        3 |     95.4% |117, 179, 227 |
| src/rememberstack/surfaces/query\_sandbox/errors.py                                           |        5 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/examples.py                                         |       67 |        1 |        6 |        1 |     97.3% |       474 |
| src/rememberstack/surfaces/query\_sandbox/executor.py                                         |      413 |       32 |      138 |       24 |     89.5% |179, 218-219, 232, 235-236, 241, 249, 283-284, 289, 295, 302, 316-321, 430, 436, 447, 449, 451, 454-456, 471, 632, 652, 676-681, 824, 857-\>exit, 1014, 1024-1025 |
| src/rememberstack/surfaces/query\_sandbox/grammar.py                                          |      658 |       53 |      254 |       33 |     89.3% |248, 252, 257, 391, 416, 464, 568-\>567, 592, 609-610, 613-622, 645, 728, 734, 786, 790-791, 798, 808, 906, 912-921, 935-938, 944-\>941, 988-\>986, 1059-\>1061, 1079, 1103-\>1107, 1123-1124, 1127, 1153-\>1151, 1160, 1208, 1255-\>1261, 1291, 1297-1303, 1309-1310, 1314-1317, 1366, 1420 |
| src/rememberstack/surfaces/query\_sandbox/layers.py                                           |       38 |        3 |       14 |        3 |     88.5% |101, 105-\>110, 108-109, 110-\>99 |
| src/rememberstack/surfaces/query\_sandbox/limits.py                                           |       23 |        0 |        6 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/nomination.py                                       |      287 |       30 |       98 |       16 |     87.5% |204-205, 210, 279-287, 294, 498-499, 563, 565, 567, 681-687, 693, 710-711, 748, 770-771, 779, 811-812, 835-\>837, 858, 885 |
| src/rememberstack/surfaces/query\_sandbox/open\_query.py                                      |       63 |        3 |       10 |        2 |     93.2% |75, 150, 210 |
| src/rememberstack/surfaces/query\_sandbox/result.py                                           |       70 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/surfaces/query\_sandbox/saved\_queries.py                                   |      585 |       64 |      240 |       56 |     84.7% |205, 223, 286, 447, 497, 503, 508, 545, 590, 601, 609, 649, 737, 803, 809, 825, 875-\>885, 943, 964, 1048, 1078, 1101, 1274, 1360, 1547-\>1549, 1662, 1667, 1751, 1919, 1925, 1945, 1958, 1963, 1972-1983, 1990, 2000, 2006, 2016, 2023, 2040, 2046-2051, 2057, 2078, 2080, 2083, 2103, 2153, 2158, 2175-2180, 2183, 2187, 2191, 2201, 2236, 2253-\>2257, 2268, 2296-2298 |
| src/rememberstack/surfaces/route\_scope.py                                                    |       25 |        0 |       14 |        0 |    100.0% |           |
| src/rememberstack/workers/\_\_init\_\_.py                                                     |       76 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/base.py                                                             |      242 |       13 |       48 |        3 |     94.5% |257-267, 269-278, 300, 417-418, 441-442, 616, 746-749 |
| src/rememberstack/workers/corpus\_export.py                                                   |      108 |        0 |       42 |        1 |     99.3% | 192-\>194 |
| src/rememberstack/workers/corpus\_publication.py                                              |      239 |       15 |       96 |       13 |     91.6% |88, 112, 202-\>198, 231, 242-243, 258, 309, 311, 319, 369, 372-\>exit, 378-\>380, 431, 433-434, 440-441, 463-\>467 |
| src/rememberstack/workers/e0.py                                                               |      778 |       60 |      192 |       25 |     90.4% |287, 413, 440, 490-496, 529, 636-644, 849, 876-881, 936-\>945, 947-956, 1131, 1164-1173, 1248-1249, 1608, 1808-1839, 1899, 2004-\>2003, 2010, 2020, 2137, 2181-2182, 2223, 2288, 2370, 2405, 2466, 2484, 2499, 2520-2522, 2531, 2565-\>2570, 2571, 2578, 2586 |
| src/rememberstack/workers/e0\_crossref.py                                                     |       21 |        1 |        2 |        1 |     91.3% |        37 |
| src/rememberstack/workers/e0\_summary.py                                                      |      399 |       47 |      118 |       22 |     84.7% |354-355, 357-361, 428, 485, 506, 524, 533, 555, 557-559, 573-574, 579, 590, 612-616, 635-639, 654, 675, 686-690, 694-723, 735-740, 750, 760, 951, 962, 973, 990, 1002-1012 |
| src/rememberstack/workers/e1.py                                                               |      263 |       31 |       80 |       14 |     83.4% |303, 329, 335-\>330, 355-\>339, 399, 413, 433, 459, 470-501, 532, 534, 569-\>571, 571-\>exit, 585-607, 634-637, 885 |
| src/rememberstack/workers/e2.py                                                               |      611 |       37 |      218 |       34 |     91.2% |489, 501, 642, 682, 711, 729, 848, 1079, 1122-\>1129, 1125, 1133-1134, 1153, 1166, 1175, 1184, 1287, 1423-\>1425, 1503-\>1502, 1516-\>1520, 1538, 1580, 1700-1701, 1702-\>1734, 1704-\>1734, 1708, 1713, 1727, 1729-\>1734, 1862, 1881, 1957, 2010, 2024, 2037, 2070-2073, 2112-2114, 2205, 2250 |
| src/rememberstack/workers/e3.py                                                               |      272 |       19 |       78 |       13 |     90.3% |229, 244, 258, 286, 347, 404, 442, 484-500, 575, 610, 621, 625, 712, 727-\>746 |
| src/rememberstack/workers/extraction\_references.py                                           |       96 |        7 |       30 |        6 |     89.7% |72-73, 97, 207, 242, 258, 260 |
| src/rememberstack/workers/fact\_window\_review.py                                             |       55 |        3 |       10 |        3 |     90.8% |65, 72, 178 |
| src/rememberstack/workers/forget.py                                                           |      170 |       10 |       38 |        4 |     93.3% |157-163, 181, 220-\>232, 308, 310, 377-378, 471-472 |
| src/rememberstack/workers/knowledge\_authored.py                                              |       77 |        5 |       16 |        3 |     91.4% |55, 109, 117, 128-129 |
| src/rememberstack/workers/knowledge\_driver.py                                                |      308 |       51 |       88 |       11 |     79.3% |211, 298-309, 546-562, 566, 642-662, 676, 680, 684, 692-698, 705-723, 747, 750-751, 753, 756-757, 785 |
| src/rememberstack/workers/knowledge\_fact\_sheet.py                                           |       41 |        2 |        2 |        1 |     93.0% |    31, 57 |
| src/rememberstack/workers/knowledge\_planner.py                                               |      136 |       13 |       20 |        7 |     87.2% |74, 108, 176, 199, 207, 209, 214, 243-251, 274-275 |
| src/rememberstack/workers/knowledge\_writer.py                                                |      157 |       12 |       22 |       11 |     87.2% |81, 101, 147, 181, 198, 203, 205, 308, 345, 351, 353, 374 |
| src/rememberstack/workers/lane\_checkpoints.py                                                |       25 |        2 |        0 |        0 |     92.0% |     48-49 |
| src/rememberstack/workers/operations.py                                                       |       14 |        0 |        0 |        0 |    100.0% |           |
| src/rememberstack/workers/p1.py                                                               |      125 |        4 |       30 |        5 |     94.2% |177, 184, 192, 253-\>265, 384 |
| src/rememberstack/workers/reconcile.py                                                        |      354 |       23 |       96 |       20 |     90.4% |121-\>126, 181, 273-274, 281, 339, 347, 356-359, 394-395, 404, 436, 473, 480, 506-507, 513, 550, 555, 589-\>581, 591, 635-\>639, 652, 846-\>850, 1326, 1349 |
| src/rememberstack/workers/section\_orientation.py                                             |       48 |        4 |       18 |        4 |     87.9% |46, 83, 95, 97 |
| src/rememberstack/workers/sync.py                                                             |       84 |        0 |       18 |        0 |    100.0% |           |
| **TOTAL**                                                                                     | **48815** | **3912** | **12618** | **1881** | **89.4%** |           |


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