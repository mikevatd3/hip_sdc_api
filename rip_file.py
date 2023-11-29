with open("census_extractomatic/api.py", "r") as f:
    lines = f.readlines()


within = False
buffer = []
result = []

for line in lines:
    if line.startswith("@app.route"):
        within = True
    if within:
        buffer.append(line)
    if line.startswith("def"):
        buffer.append("    pass")
        buffer.append("\n")
        buffer.append("\n")

        within = False
        result.append("".join(buffer))
        buffer = []

with open("census_extractomatic/_api/routes.py", "w") as f:
    f.write("\n".join(result))
