def arrange_variable_hierarchy(data):
    result = []
    stack = []

    for item in data:
        current_node = {
            "column_id": item.column_id,
            "column_title": item.column_title,
            "indent": item.indent,
            "children": []
        }

        while stack and stack[-1]["indent"] >= item["indent"]:
            stack.pop()

        if stack:
            stack[-1]["node"]["children"].append(current_node)

        else:
            result.append(current_node)

        stack.append({"indent": item["indent"], "node": current_node})

    return result
