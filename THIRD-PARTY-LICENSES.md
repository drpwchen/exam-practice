# 第三方授權 Third-Party Licenses

本專案的程式碼以 [PolyForm Noncommercial License 1.0.0](LICENSE) 釋出，但內含或
相依下列第三方元件，各自保有其原始授權。下列授權**不受**本專案非商業條款限制。

This project's own source code is licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE). It bundles or depends on the
third-party components below, each under its own original license. These
licenses are **not** restricted by this project's noncommercial terms.

---

## FSRS — `fsrs` (py-fsrs)

- **用途 Use**: 間隔重複排程演算法 / spaced-repetition scheduling engine
- **專案 Project**: <https://github.com/open-spaced-repetition/py-fsrs>
- **授權 License**: MIT License
- **版權 Copyright**: Copyright (c) Open Spaced Repetition

```
MIT License

Copyright (c) Open Spaced Repetition

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

> 註：FSRS 的 MIT 授權為 permissive，允許在閉源、商業或本專案的自訂授權下使用，
> 唯一義務是保留上述版權與授權聲明（即本檔案）。
>
> Note: FSRS's MIT license is permissive — it allows use in closed-source,
> commercial, or this project's custom-licensed context. The only obligation is
> to retain the copyright and license notice above (i.e. this file).

---

其餘相依套件（FastAPI、SQLAlchemy、Vue、Vant、Pinia、Vite、PyMuPDF、Pillow 等）
均以各自的 OSI 開源授權（多為 MIT / BSD / Apache-2.0 / 部分 LGPL）散布，透過
`pip` 與 `npm` 安裝，授權檔隨各套件提供。本專案未修改、未重新散布其原始碼。

The remaining dependencies (FastAPI, SQLAlchemy, Vue, Vant, Pinia, Vite,
PyMuPDF, Pillow, etc.) are distributed under their own OSI-approved licenses
(mostly MIT / BSD / Apache-2.0, some LGPL), installed via `pip` and `npm`, with
license files shipped alongside each package. This project neither modifies nor
redistributes their source.
