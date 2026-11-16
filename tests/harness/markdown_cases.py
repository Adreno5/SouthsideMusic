from __future__ import annotations

from typing import Any


_COVER_PNG = (
    'iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAEElEQVR4nGNQ7L4EQQxwFgBV'
    'dgj1V+uyfgAAAABJRU5ErkJggg=='
)

MARKDOWN_CASES: list[dict[str, Any]] = [
    {
        'id': 'empty',
        'source': '',
        'html_contains': [],
        'plain_contains': [],
    },
    *[
        {
            'id': f'atx_heading_{level}',
            'source': f'{"#" * level} Heading {level}',
            'html_contains': [f'<h{level}>', f'Heading {level}', f'</h{level}>'],
            'plain_contains': [f'Heading {level}'],
        }
        for level in range(1, 7)
    ],
    {
        'id': 'setext_headings',
        'source': 'First heading\n=============\n\nSecond heading\n--------------',
        'html_contains': ['<h1>First heading</h1>', '<h2>Second heading</h2>'],
        'plain_contains': ['First heading', 'Second heading'],
    },
    {
        'id': 'paragraphs',
        'source': 'First paragraph.\n\nSecond paragraph.',
        'html_contains': ['<p>First paragraph.</p>', '<p>Second paragraph.</p>'],
        'plain_contains': ['First paragraph.', 'Second paragraph.'],
    },
    *[
        {
            'id': f'{kind}_break',
            'source': f'First line{separator}Second line',
            'html_contains': ['First line', '<br', 'Second line'],
            'plain_contains': ['First line\nSecond line'],
        }
        for kind, separator in [
            ('soft', '\n'),
            ('two_space', '  \n'),
            ('backslash', '\\\n'),
        ]
    ],
    {
        'id': 'emphasis',
        'source': '**bold** __strong__ *italic* _emphasis_ ~~deleted~~',
        'html_contains': [
            '<strong>bold</strong>',
            '<strong>strong</strong>',
            '<em>italic</em>',
            '<em>emphasis</em>',
            '<s>deleted</s>',
        ],
        'plain_contains': ['bold strong italic emphasis deleted'],
        'plain_not_contains': ['**', '~~'],
    },
    {
        'id': 'nested_emphasis',
        'source': '***both*** and **bold with *italic* inside**',
        'html_contains': ['<em>', '<strong>', 'both', 'italic'],
        'plain_contains': ['both and bold with italic inside'],
    },
    {
        'id': 'unordered_list_markers',
        'source': '- Dash item\n\n* Star item\n\n+ Plus item',
        'html_contains': ['<ul>', '<li>', 'Dash item', 'Star item', 'Plus item'],
        'plain_contains': ['Dash item', 'Star item', 'Plus item'],
    },
    {
        'id': 'ordered_list_start',
        'source': '3. Third item\n4. Fourth item',
        'html_contains': ['<ol start="3">', '<li>Third item</li>', 'Fourth item'],
        'plain_contains': ['Third item', 'Fourth item'],
    },
    {
        'id': 'nested_lists',
        'source': (
            '- Parent item\n'
            '  - Child item\n'
            '    1. Grandchild item\n'
            '  - Second child\n'
            '- Next parent'
        ),
        'html_contains': ['<ul>', '<ol>', 'Grandchild item', 'Second child'],
        'plain_contains': [
            'Parent item',
            'Child item',
            'Grandchild item',
            'Second child',
            'Next parent',
        ],
    },
    {
        'id': 'loose_list',
        'source': '- First item\n\n  Another paragraph.\n\n- Second item',
        'html_contains': ['<li>', '<p>First item</p>', '<p>Another paragraph.</p>'],
        'plain_contains': ['First item', 'Another paragraph.', 'Second item'],
    },
    {
        'id': 'blockquote',
        'source': '> Quoted **bold** text.\n>\n> Another quoted paragraph.',
        'html_contains': ['<blockquote>', '<strong>bold</strong>', '</blockquote>'],
        'plain_contains': ['Quoted bold text.', 'Another quoted paragraph.'],
    },
    {
        'id': 'nested_blockquote',
        'source': '> Outer quote\n>\n> > Inner quote\n> >\n> > - Quoted item',
        'html_contains': ['<blockquote>', '<ul>', 'Inner quote', 'Quoted item'],
        'plain_contains': ['Outer quote', 'Inner quote', 'Quoted item'],
    },
    {
        'id': 'thematic_break',
        'source': 'Before rule\n\n---\n\nAfter rule',
        'html_contains': ['Before rule', '<hr', 'After rule'],
        'plain_contains': ['Before rule', 'After rule'],
    },
    {
        'id': 'table',
        'source': (
            '| Name | Count |\n| :--- | ---: |\n| **Track** | 7 |\n| Song | 12 |'
        ),
        'html_contains': [
            '<table>',
            '<thead>',
            '<tbody>',
            '<th',
            '<td',
            '<strong>Track</strong>',
        ],
        'plain_contains': ['Name', 'Count', 'Track', '7', 'Song', '12'],
    },
    {
        'id': 'table_escaped_pipe',
        'source': '| Value |\n| --- |\n| left\\|right |',
        'html_contains': ['<table>', '<td>left|right</td>'],
        'plain_contains': ['Value', 'left|right'],
    },
    {
        'id': 'inline_link',
        'source': '[Southside](https://example.invalid/music "Music page")',
        'html_contains': [
            '<a href="https://example.invalid/music"',
            'title="Music page"',
            '>Southside</a>',
        ],
        'plain_contains': ['Southside'],
        'plain_not_contains': ['https://example.invalid/music'],
    },
    {
        'id': 'reference_link',
        'source': '[Reference][music]\n\n[music]: https://example.invalid/reference',
        'html_contains': ['href="https://example.invalid/reference"', '>Reference</a>'],
        'plain_contains': ['Reference'],
        'plain_not_contains': ['[music]'],
    },
    {
        'id': 'autolinks',
        'source': '<https://example.invalid/path> and <music@example.invalid>',
        'html_contains': [
            'href="https://example.invalid/path"',
            'href="mailto:music@example.invalid"',
        ],
        'plain_contains': ['https://example.invalid/path', 'music@example.invalid'],
    },
    {
        'id': 'local_image',
        'source': 'Before ![Local cover](markdown-cover.png "Cover") after.',
        'html_contains': [
            '<img',
            'src="markdown-cover.png"',
            'alt="Local cover"',
            'title="Cover"',
        ],
        'plain_contains': ['Before', 'after.'],
        'image_files': {'markdown-cover.png': _COVER_PNG},
    },
    {
        'id': 'inline_image',
        'source': f'Before ![Inline cover](data:image/png;base64,{_COVER_PNG}) after.',
        'html_contains': ['<img', 'src="data:image/png;base64,', 'alt="Inline cover"'],
        'plain_contains': ['Before', 'after.'],
    },
    {
        'id': 'linked_image',
        'source': '[![Cover](markdown-cover.png)](https://example.invalid/album)',
        'html_contains': [
            'href="https://example.invalid/album"',
            '<img',
            'alt="Cover"',
        ],
        'plain_contains': [],
        'image_files': {'markdown-cover.png': _COVER_PNG},
    },
    {
        'id': 'fenced_code',
        'source': '```python\nprint("<tag> & value")\n```',
        'html_contains': [
            '<pre><code class="language-python">',
            'print(&quot;&lt;tag&gt; &amp; value&quot;)',
            '</code></pre>',
        ],
        'plain_contains': ['print("<tag> & value")'],
        'html_not_contains': ['<tag>'],
    },
    {
        'id': 'tilde_fence',
        'source': '~~~text\n**literal** <b>code</b>\n~~~',
        'html_contains': ['language-text', '**literal**', '&lt;b&gt;code&lt;/b&gt;'],
        'plain_contains': ['**literal** <b>code</b>'],
        'html_not_contains': ['<strong>', '<b>'],
    },
    {
        'id': 'long_fence',
        'source': '````text\n```\nstill code\n````',
        'html_contains': ['<pre><code', '```', 'still code'],
        'plain_contains': ['```\nstill code'],
    },
    {
        'id': 'indented_code',
        'source': 'Paragraph.\n\n    first = 1\n    second = 2',
        'html_contains': ['<pre><code>', 'first = 1', 'second = 2'],
        'plain_contains': ['Paragraph.', 'first = 1\nsecond = 2'],
    },
    {
        'id': 'inline_code',
        'source': 'Use `**literal** <tag>` and ``a ` backtick``.',
        'html_contains': [
            '<code>**literal** &lt;tag&gt;</code>',
            '<code>a ` backtick</code>',
        ],
        'plain_contains': ['Use **literal** <tag> and a ` backtick.'],
        'html_not_contains': ['<strong>', '<tag>'],
    },
    {
        'id': 'escapes_and_entities',
        'source': r'\*literal\* [brackets] \# heading &amp; &lt;tag&gt; \\slash',
        'html_contains': [
            '*literal*',
            '[brackets]',
            '# heading',
            '&amp;',
            '&lt;tag&gt;',
        ],
        'plain_contains': ['*literal* [brackets] # heading & <tag> \\slash'],
        'html_not_contains': ['<em>', '<h1>', '<tag>'],
    },
    {
        'id': 'raw_html_disabled',
        'source': '<b>literal</b> <script>alert("x")</script>\n\n<div>block</div>',
        'html_contains': [
            '&lt;b&gt;literal&lt;/b&gt;',
            '&lt;script&gt;',
            '&lt;div&gt;block&lt;/div&gt;',
        ],
        'plain_contains': [
            '<b>literal</b>',
            '<script>alert("x")</script>',
            '<div>block</div>',
        ],
        'html_not_contains': ['<b>', '<script>', '<div>'],
    },
    {
        'id': 'unicode',
        'source': '# 南方音乐 🎵\n\n中文、café、e\u0301、العربية、👩\u200d💻。',
        'html_contains': [
            '<h1>',
            '南方音乐 🎵',
            '中文',
            'café',
            'e\u0301',
            'العربية',
            '👩\u200d💻',
        ],
        'plain_contains': ['南方音乐 🎵', '中文、café、e\u0301、العربية、👩\u200d💻。'],
    },
    {
        'id': 'math_inline_dollar',
        'source': r'Before $x^2 + y^2$ after.',
        'html_contains': ['class="math-inline"', 'alt="x^2 + y^2"'],
        'plain_contains': ['Before', 'after.'],
        'math_inline': ['x^2 + y^2'],
    },
    {
        'id': 'math_inline_parentheses',
        'source': r'Before \(\frac{1}{2}\) after.',
        'html_contains': ['class="math-inline"', r'alt="\frac{1}{2}"'],
        'plain_contains': ['Before', 'after.'],
        'math_inline': [r'\frac{1}{2}'],
    },
    {
        'id': 'math_block_dollars',
        'source': 'Before block.\n\n$$E = mc^2$$\n\nAfter block.',
        'html_contains': ['class="math-block"', 'alt="E = mc^2"'],
        'plain_contains': ['Before block.', 'After block.'],
        'math_block': ['E = mc^2'],
    },
    {
        'id': 'math_block_brackets',
        'source': r'Before block.\[\sum_{i=1}^{n} i\]After block.',
        'html_contains': ['class="math-block"', r'alt="\sum_{i=1}^{n} i"'],
        'plain_contains': ['Before block.', 'After block.'],
        'math_block': [r'\sum_{i=1}^{n} i'],
    },
    {
        'id': 'math_multiline_block',
        'source': 'Before.\n\n$$\nx^2 +\ny^2\n$$\n\nAfter.',
        'html_contains': ['class="math-block"'],
        'plain_contains': ['Before.', 'After.'],
        'math_block': ['x^2 +\ny^2'],
    },
    {
        'id': 'math_repeated_and_mixed',
        'source': r'First $x$ then $x$ and \(y\), finally $$z^2$$.',
        'html_contains': ['class="math-inline"', 'class="math-block"'],
        'plain_contains': ['First', 'then', 'and', 'finally'],
        'math_inline': ['x', 'x', 'y'],
        'math_block': ['z^2'],
    },
    {
        'id': 'math_in_list_and_quote',
        'source': '- Formula $x^2$\n\n> Another \\(y^2\\)',
        'html_contains': ['<ul>', '<blockquote>', 'class="math-inline"'],
        'plain_contains': ['Formula', 'Another'],
        'math_inline': ['x^2', 'y^2'],
    },
    *[
        {
            'id': f'{language}_fence_plain_text',
            'source': f'Before.\n\n```{language}\nx^2 + y^2 = z^2\n```\n\nAfter.',
            'html_contains': ['class="latex-output"', 'x^2 + y^2 = z^2'],
            'plain_contains': ['Before.', 'x^2 + y^2 = z^2', 'After.'],
            'html_not_contains': ['<pre>', '<img'],
        }
        for language in ['latex', 'math', 'tex']
    ],
    *[
        {
            'id': f'{language}_fence_empty',
            'source': f'Before.\n\n```{language}\n\n```\n\nAfter.',
            'html_contains': ['class="latex-output"'],
            'plain_contains': ['Before.', 'After.'],
            'html_not_contains': ['<img', 'math-inline', 'math-block'],
            'math_inline': [],
            'math_block': [],
        }
        for language in ['latex', 'math', 'tex']
    ],
    {
        'id': 'latex_fence_markdown',
        'source': '```latex\n## Result\n\nInline $x$ and block $$y^2$$\n```',
        'html_contains': [
            '<h2>Result</h2>',
            'class="math-inline"',
            'class="math-block"',
        ],
        'plain_contains': ['Result', 'Inline', 'and block'],
        'math_inline': ['x'],
        'math_block': ['y^2'],
    },
    {
        'id': 'dollars_in_inline_code',
        'source': r'`$x$ $$y$$ \(z\) \[w\]` then $outside$.',
        'html_contains': [r'<code>$x$ $$y$$ \(z\) \[w\]</code>'],
        'plain_contains': [r'$x$ $$y$$ \(z\) \[w\]', 'then'],
        'math_inline': ['outside'],
    },
    {
        'id': 'dollars_in_multibacktick_code',
        'source': '``a ` $x$`` then $outside$.',
        'html_contains': ['<code>a ` $x$</code>'],
        'plain_contains': ['a ` $x$', 'then'],
        'math_inline': ['outside'],
    },
    *[
        {
            'id': f'dollars_in_{kind}_fence',
            'source': f'{marker}python\n$x$ $$y$$ \\(z\\) \\[w\\]\n{marker}\n\n$outside$',
            'html_contains': [r'$x$ $$y$$ \(z\) \[w\]', 'language-python'],
            'plain_contains': [r'$x$ $$y$$ \(z\) \[w\]'],
            'math_inline': ['outside'],
        }
        for kind, marker in [('backtick', '```'), ('tilde', '~~~')]
    ],
    *[
        {
            'id': f'dollars_in_{kind}_code',
            'source': f'{indent}value = "$x$ $$y$$ \\(z\\) \\[w\\]"\n',
            'html_contains': ['<pre><code>', r'$x$ $$y$$ \(z\) \[w\]'],
            'plain_contains': [r'value = "$x$ $$y$$ \(z\) \[w\]"'],
            'html_not_contains': ['math-inline', 'math-block', '<img'],
        }
        for kind, indent in [('indented', '    '), ('tab_indented', '\t')]
    ],
    {
        'id': 'dollars_in_multiline_code_span',
        'source': '`first\n$x$ second` then $outside$.',
        'html_contains': ['<code>first $x$ second</code>'],
        'plain_contains': ['first $x$ second', 'then'],
        'math_inline': ['outside'],
    },
    {
        'id': 'dollars_in_nested_fence',
        'source': '> ```python\n> value = "$x$"\n> ```',
        'html_contains': ['<blockquote>', '<pre><code', '$x$'],
        'plain_contains': ['value = "$x$"'],
        'html_not_contains': ['math-inline', '<img'],
    },
    {
        'id': 'escaped_and_unpaired_dollars',
        'source': r'Cost \$5 and \$10. A lone $ remains.',
        'html_contains': ['Cost $5 and $10.', 'A lone $ remains.'],
        'plain_contains': ['Cost $5 and $10. A lone $ remains.'],
        'html_not_contains': ['math-inline', '<img'],
    },
    {
        'id': 'dollars_in_link_destination',
        'source': '[Price](https://example.invalid/$price$) and $x$.',
        'html_contains': ['href="https://example.invalid/$price$"', '>Price</a>'],
        'plain_contains': ['Price', 'and'],
        'math_inline': ['x'],
    },
    {
        'id': 'math_in_link_label',
        'source': '[Value $x$](https://example.invalid/value "Cost $5$")',
        'html_contains': [
            'href="https://example.invalid/value"',
            'title="Cost $5$"',
            'class="math-inline"',
        ],
        'plain_contains': ['Value'],
        'math_inline': ['x'],
    },
    {
        'id': 'escaped_math_delimiters',
        'source': r'Before \\(literal\\) and \\[literal\\] after.',
        'html_contains': [r'\(literal\)', r'\[literal\]'],
        'plain_contains': [r'Before \(literal\) and \[literal\] after.'],
        'html_not_contains': ['<img', 'math-inline', 'math-block'],
    },
    {
        'id': 'dollars_in_image_alt',
        'source': 'Before ![Cost $5$](markdown-cover.png) after.',
        'html_contains': ['src="markdown-cover.png"', 'alt="Cost $5$"'],
        'plain_contains': ['Before', 'after.'],
        'html_not_contains': ['math-inline', 'math-block'],
        'image_files': {'markdown-cover.png': _COVER_PNG},
    },
    {
        'id': 'literal_math_placeholder',
        'source': 'Literal SOUTHSIDEMATHTOKEN0 and $x$.',
        'html_contains': ['Literal SOUTHSIDEMATHTOKEN0'],
        'plain_contains': ['Literal SOUTHSIDEMATHTOKEN0', 'and'],
        'math_inline': ['x'],
    },
    {
        'id': 'invalid_math',
        'source': r'Before $\frac{$ after.',
        'html_contains': [r'\frac{', 'Before', 'after.'],
        'plain_contains': [r'Before \frac{ after.'],
        'html_not_contains': ['data:image/svg+xml'],
    },
    {
        'id': 'invalid_math_escaped',
        'source': r'Before $\frac{<&$ after.',
        'html_contains': [r'\frac{&lt;&amp;', 'Before', 'after.'],
        'plain_contains': [r'Before \frac{<& after.'],
        'html_not_contains': ['data:image/svg+xml', '<&'],
    },
    *[
        {
            'id': f'incomplete_{kind}',
            'source': source,
            'html_contains': [html_fragment],
            'plain_contains': [plain_fragment],
            'html_not_contains': ['data:image/svg+xml'],
        }
        for kind, source, html_fragment, plain_fragment in [
            ('bold', '**unfinished', '**unfinished', '**unfinished'),
            ('italic', '*unfinished', '*unfinished', '*unfinished'),
            ('strike', '~~unfinished', '~~unfinished', '~~unfinished'),
            (
                'link',
                '[unfinished](https://',
                '[unfinished](https://',
                '[unfinished](https://',
            ),
            ('image', '![unfinished](', '![unfinished](', '![unfinished]('),
            ('code_span', '`unfinished', '`unfinished', '`unfinished'),
            ('dollar_math', '$x^2', '$x^2', '$x^2'),
            ('block_math', '$$x^2', '$$x^2', '$$x^2'),
            ('parenthesis_math', r'\(x^2', '(x^2', '(x^2'),
            ('bracket_math', r'\[x^2', '[x^2', '[x^2'),
            ('table', '| A | B |\n| --- |', '| A | B |', '| A | B |'),
        ]
    ],
    {
        'id': 'incomplete_code_fence',
        'source': '```python\nprint("stream")\n$x$\n',
        'html_contains': ['<pre><code class="language-python">', '$x$'],
        'plain_contains': ['print("stream")\n$x$'],
        'html_not_contains': ['math-inline', '<img'],
    },
]
