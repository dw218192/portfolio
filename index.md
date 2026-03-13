<!--- [Switch to Chinese/切换到中文](/portfolio/index_cn) --->

{% for cat in site.portfolio_sections %}
<h1 id="{{ cat.id }}">{{ cat.title }}</h1>

---

{% assign items = site.projects | where: "category", cat.id | sort: "order" %}
{% for project in items %}
## [{{ project.title }}]({{ project.link }})
{% for m in project.media %}
{% if m.type == "video" %}
<video class="project-media" controls>
  <source src="{{ m.src }}" type="video/mp4">
</video>
{% elsif m.type == "image" %}
<img class="project-media" src="{{ m.src | relative_url }}" alt="{{ project.title }}" />
{% endif %}
{% endfor %}

{{ project.content }}

{% endfor %}
{% endfor %}
