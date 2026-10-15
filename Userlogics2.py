 if name_changed:
        # data.name has already been normalized by Pydantic.
        method.name = data.name
        method.slug = generate_slug(data.name)

        updated_fields.extend(["name", "slug"])


Cannot assign to attribute "name" for class "PaymentMethods"
  Type "str | None" is not assignable to type "str"
    "None" is not assignable to "str"PylancereportAttributeAccessIssue

Argument of type "str | None" cannot be assigned to parameter "name" of type "str" in function "generate_slug"
  Type "str | None" is not assignable to type "str"
   
